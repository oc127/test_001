"""Supplier lead-time learner.

Why this exists
---------------
Every factory lies about lead time. "15 天交货" in the contract often means
20-28 天 in reality once you factor in QC rework, material shortages, and
holiday slowdowns. Over-trusting the promised lead time is the #1 cause of
stockouts for Chinese sourcing.

This module analyses the history of purchase orders (order_date →
actual_arrival_date) to compute the **observed** lead time per supplier, per
product line, with a confidence interval. The reorder engine can then use
observed lead time instead of the product's nominal value.

Learning strategy
-----------------
For each supplier:
1. Collect all POs with both order_date and actual_arrival_date.
2. Compute delta_days = (actual_arrival - order_date).
3. Strip out outliers (top/bottom 10% by default) to avoid being dragged by
   a single cargo disaster.
4. Return mean, stddev, p90 (conservative upper bound for planning).
5. Flag the supplier as "reliable" if stddev < 5 days, else "volatile".

Also emits a per-SKU profile so that one SKU ordered from two different
suppliers doesn't get averaged into nonsense.

Only POs that have COMPLETED (actual_arrival_date set) are included. Draft
and in-progress POs are ignored.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from statistics import mean, median, stdev
from typing import Iterable, Optional

from ..models import Product, PurchaseOrder


# How many historical POs we need before we trust the observed lead time.
MIN_SAMPLES_FOR_TRUST = 3

# Supplier is "reliable" if observed stddev is below this many days.
RELIABLE_STDDEV_THRESHOLD_DAYS = 5.0


@dataclass
class SupplierLeadTimeProfile:
    """Observed lead time statistics for a supplier (optionally scoped to a SKU)."""

    supplier: str
    sku: Optional[str] = None  # None = aggregated across all SKUs
    sample_size: int = 0
    mean_days: float = 0.0
    median_days: float = 0.0
    stddev_days: float = 0.0
    p90_days: float = 0.0  # 90th percentile — use this for conservative planning
    min_days: int = 0
    max_days: int = 0
    is_reliable: bool = False
    latest_po_date: Optional[date] = None
    raw_samples: list[int] = field(default_factory=list)

    @property
    def planning_lead_days(self) -> int:
        """The lead time value the reorder engine should use.

        If we have enough data, use p90 (conservative). Otherwise fall back
        to the mean. Caller should combine this with product's nominal value
        if sample_size is below MIN_SAMPLES_FOR_TRUST.
        """
        if self.sample_size == 0:
            return 0
        return int(round(self.p90_days if self.sample_size >= MIN_SAMPLES_FOR_TRUST else self.mean_days))

    @property
    def trust_level(self) -> str:
        if self.sample_size == 0:
            return "unknown"
        if self.sample_size < MIN_SAMPLES_FOR_TRUST:
            return "low"
        return "high" if self.is_reliable else "medium"

    def to_note(self) -> str:
        """Short human-readable summary for the alert notes."""
        if self.sample_size == 0:
            return f"{self.supplier}: 无历史数据"
        trust = {"low": "样本少", "medium": "波动大", "high": "稳定"}[self.trust_level]
        return (
            f"{self.supplier}: 观察 lead {self.mean_days:.0f}±{self.stddev_days:.0f} 天 "
            f"(p90={self.p90_days:.0f}, 样本 {self.sample_size}, {trust})"
        )


def _completed_pos(pos: Iterable[PurchaseOrder]) -> list[PurchaseOrder]:
    return [p for p in pos if p.actual_arrival_date and p.order_date and p.supplier]


def _percentile(values: list[float], pct: float) -> float:
    """Simple nearest-rank percentile (no interpolation). Values pre-sorted ok."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[k]


def _strip_outliers(values: list[int], trim_pct: float = 0.1) -> list[int]:
    """Remove top and bottom `trim_pct` of a sample."""
    if len(values) < 5:
        return values[:]
    ordered = sorted(values)
    k = int(len(ordered) * trim_pct)
    return ordered[k : len(ordered) - k] if k > 0 else ordered[:]


def _profile_from_samples(
    supplier: str,
    samples: list[int],
    latest_po_date: Optional[date],
    sku: Optional[str] = None,
) -> SupplierLeadTimeProfile:
    if not samples:
        return SupplierLeadTimeProfile(supplier=supplier, sku=sku)
    trimmed = _strip_outliers(samples, trim_pct=0.1)
    sd = stdev(trimmed) if len(trimmed) >= 2 else 0.0
    return SupplierLeadTimeProfile(
        supplier=supplier,
        sku=sku,
        sample_size=len(samples),
        mean_days=round(mean(trimmed), 1),
        median_days=round(median(trimmed), 1),
        stddev_days=round(sd, 1),
        p90_days=round(_percentile(trimmed, 90), 1),
        min_days=min(samples),
        max_days=max(samples),
        is_reliable=sd < RELIABLE_STDDEV_THRESHOLD_DAYS,
        latest_po_date=latest_po_date,
        raw_samples=samples,
    )


def learn_supplier_lead_times(
    pos: list[PurchaseOrder],
) -> dict[str, SupplierLeadTimeProfile]:
    """Aggregate POs across SKUs to produce one profile per supplier.

    Use this for the high-level "which suppliers are reliable?" dashboard.
    """
    completed = _completed_pos(pos)
    by_supplier: dict[str, list[int]] = defaultdict(list)
    latest_by_supplier: dict[str, date] = {}

    for po in completed:
        days = (po.actual_arrival_date - po.order_date).days
        if days <= 0:
            continue  # data error — handled by data_quality
        by_supplier[po.supplier].append(days)
        if po.supplier not in latest_by_supplier or po.order_date > latest_by_supplier[po.supplier]:
            latest_by_supplier[po.supplier] = po.order_date

    return {
        sup: _profile_from_samples(sup, samples, latest_by_supplier.get(sup))
        for sup, samples in by_supplier.items()
    }


def learn_per_sku_lead_times(
    pos: list[PurchaseOrder],
) -> dict[tuple[str, str], SupplierLeadTimeProfile]:
    """Per (supplier, sku) profiles — finer grain.

    Same SKU ordered from two factories can have different lead times. For
    reorder engine purposes, the (supplier, sku) profile is more accurate
    than the supplier-wide aggregate.
    """
    completed = _completed_pos(pos)
    by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    latest: dict[tuple[str, str], date] = {}

    for po in completed:
        days = (po.actual_arrival_date - po.order_date).days
        if days <= 0:
            continue
        key = (po.supplier, po.sku)
        by_key[key].append(days)
        if key not in latest or po.order_date > latest[key]:
            latest[key] = po.order_date

    return {
        key: _profile_from_samples(
            supplier=key[0], samples=samples, latest_po_date=latest.get(key), sku=key[1]
        )
        for key, samples in by_key.items()
    }


def resolve_effective_lead_days(
    product: Product,
    profiles_per_sku: dict[tuple[str, str], SupplierLeadTimeProfile],
    profiles_per_supplier: dict[str, SupplierLeadTimeProfile],
) -> tuple[int, str]:
    """Pick the best lead time estimate for a product.

    Priority:
    1. (supplier, sku) profile with enough samples → use p90
    2. supplier-wide profile with enough samples → use p90, plus 10% safety
    3. Product's nominal total_lead_days (from config)

    Returns (lead_days, reason).
    """
    supplier = product.default_supplier
    nominal = product.total_lead_days

    if supplier:
        per_sku_key = (supplier, product.sku)
        if per_sku_key in profiles_per_sku:
            prof = profiles_per_sku[per_sku_key]
            if prof.sample_size >= MIN_SAMPLES_FOR_TRUST:
                effective = prof.planning_lead_days
                reason = (
                    f"使用 {supplier} × {product.sku} 历史：p90={effective} 天 "
                    f"(n={prof.sample_size}, σ={prof.stddev_days})"
                )
                return max(effective, 1), reason

        if supplier in profiles_per_supplier:
            prof = profiles_per_supplier[supplier]
            if prof.sample_size >= MIN_SAMPLES_FOR_TRUST:
                # Supplier-wide is less specific → add 10% safety buffer
                effective = int(round(prof.planning_lead_days * 1.1))
                reason = (
                    f"使用供应商 {supplier} 历史：p90×1.1={effective} 天 "
                    f"(n={prof.sample_size})"
                )
                return max(effective, 1), reason

    return nominal, f"使用默认 lead time {nominal} 天（无足够历史数据）"


def compare_promised_vs_actual(
    products: list[Product],
    pos: list[PurchaseOrder],
) -> list[dict]:
    """Report of promise vs reality per product, sorted by worst slip first.

    Use in monthly review to call out suppliers that consistently over-promise.
    """
    per_sku = learn_per_sku_lead_times(pos)
    product_by_sku = {p.sku: p for p in products}
    rows: list[dict] = []

    for (supplier, sku), prof in per_sku.items():
        product = product_by_sku.get(sku)
        if product is None:
            continue
        promised = product.total_lead_days
        slip = prof.mean_days - promised
        rows.append(
            {
                "sku": sku,
                "product_name": product.name,
                "supplier": supplier,
                "promised_days": promised,
                "observed_mean_days": prof.mean_days,
                "observed_p90_days": prof.p90_days,
                "slip_days": round(slip, 1),
                "sample_size": prof.sample_size,
                "is_reliable": prof.is_reliable,
                "verdict": (
                    "✅ 守时" if slip <= 2
                    else "⚠️ 轻微延迟" if slip <= 7
                    else "🔴 严重延迟"
                ),
            }
        )

    rows.sort(key=lambda r: -r["slip_days"])
    return rows
