"""1688 supplier price monitor using Firecrawl.

Why this exists
---------------
If a supplier quietly raises prices, our COGS silently climbs and margins
evaporate. Conversely, if a supplier LOWERS prices, the user is overpaying.
Manually checking 20+ product pages every week is tedious; Firecrawl can
scrape them in seconds.

Each product in the master data has an optional `supplier_1688_url`. This
module:

1. For each product with a URL, scrape the page markdown via Firecrawl.
2. Extract the price using a few regex patterns (1688 prices appear as
   "¥12.50" or "12.50元" or tiered-pricing tables).
3. Compare to the stored unit_cost_rmb.
4. Emit PriceAlert records when change_pct exceeds threshold.

Firecrawl is already a dependency in repo — see `firecrawl-py` or use the
HTTP API directly.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional

import httpx

from ..config import SETTINGS
from ..models import PriceAlert, Product

logger = logging.getLogger(__name__)

FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"

# Patterns in descending specificity
_PRICE_PATTERNS = [
    re.compile(r"¥\s*([\d]+\.?\d*)"),
    re.compile(r"([\d]+\.?\d*)\s*元"),
    re.compile(r'"price"\s*:\s*"?([\d]+\.?\d*)"?'),
    re.compile(r"单价[::]\s*([\d]+\.?\d*)"),
    re.compile(r"¥([\d]+\.?\d*)\s*-\s*¥?([\d]+\.?\d*)"),  # price range
]


@dataclass
class ScrapeResult:
    sku: str
    url: str
    markdown: str
    prices_found: list[Decimal]
    min_price: Optional[Decimal]
    scraped_at: datetime


def _clean_price(raw: str) -> Optional[Decimal]:
    try:
        d = Decimal(raw.strip())
        if 0 < d < Decimal("100000"):  # sanity check
            return d
    except Exception:
        pass
    return None


def extract_prices_from_markdown(markdown: str) -> list[Decimal]:
    """Return all plausible RMB prices found in a page's markdown."""
    found: list[Decimal] = []
    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(markdown):
            for group in match.groups():
                if group:
                    price = _clean_price(group)
                    if price is not None:
                        found.append(price)
    # Dedupe while preserving order
    seen: set[Decimal] = set()
    unique: list[Decimal] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


class FirecrawlClient:
    """Minimal Firecrawl HTTP client — we only need /scrape."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = FIRECRAWL_BASE,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or SETTINGS.firecrawl_api_key or os.getenv("FIRECRAWL_API_KEY", "")
        self.base_url = base_url
        self.timeout = timeout

    def scrape_markdown(self, url: str) -> Optional[str]:
        if not self.api_key:
            logger.warning("FIRECRAWL_API_KEY not set; skipping scrape %s", url)
            return None
        try:
            resp = httpx.post(
                f"{self.base_url}/scrape",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "waitFor": 2000,
                },
                timeout=self.timeout,
            )
            data = resp.json()
            if not data.get("success"):
                logger.warning("Firecrawl scrape failed for %s: %s", url, data)
                return None
            return data.get("data", {}).get("markdown", "")
        except Exception as exc:
            logger.exception("Firecrawl scrape error for %s: %s", url, exc)
            return None


class SupplierPriceMonitor:
    """Orchestrates: scrape → extract → compare → emit alerts."""

    def __init__(
        self,
        firecrawl: Optional[FirecrawlClient] = None,
        alert_threshold_pct: float = 5.0,
    ):
        self.firecrawl = firecrawl or FirecrawlClient()
        self.alert_threshold_pct = alert_threshold_pct

    def scrape_product(self, product: Product) -> Optional[ScrapeResult]:
        if not product.supplier_1688_url:
            return None
        md = self.firecrawl.scrape_markdown(product.supplier_1688_url)
        if md is None:
            return None
        prices = extract_prices_from_markdown(md)
        return ScrapeResult(
            sku=product.sku,
            url=product.supplier_1688_url,
            markdown=md,
            prices_found=prices,
            min_price=min(prices) if prices else None,
            scraped_at=datetime.now(),
        )

    def compare_and_alert(
        self,
        product: Product,
        scrape: ScrapeResult,
    ) -> Optional[PriceAlert]:
        if scrape.min_price is None:
            return None
        if product.unit_cost_rmb is None:
            # First-time tracking — no diff to compute, but record the value
            return None

        old = product.unit_cost_rmb
        new = scrape.min_price
        if old == 0:
            return None

        change_pct = float((new - old) / old * 100)
        if abs(change_pct) < self.alert_threshold_pct:
            return None

        return PriceAlert(
            sku=product.sku,
            supplier=product.default_supplier or "(未指定)",
            old_price_rmb=old,
            new_price_rmb=new,
            change_pct=round(change_pct, 2),
            change_direction="up" if change_pct > 0 else "down",
            url=scrape.url,
            detected_at=scrape.scraped_at,
            recommendation=(
                "⚠️ 成本上涨，更新 COGS 并检查售价" if change_pct > 0
                else "🎉 成本下降，与供应商确认是否长期价格，并考虑补货降本"
            ),
        )

    def scan_all(self, products: Iterable[Product]) -> list[PriceAlert]:
        alerts: list[PriceAlert] = []
        for product in products:
            if not product.supplier_1688_url:
                continue
            scrape = self.scrape_product(product)
            if scrape is None:
                continue
            alert = self.compare_and_alert(product, scrape)
            if alert:
                alerts.append(alert)
        return alerts
