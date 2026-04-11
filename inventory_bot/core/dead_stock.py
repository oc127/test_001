"""Dead stock / declining velocity detector.

Why this exists
---------------
The main reorder engine only answers "need more". This module answers the
opposite: "stop buying, you already have too much / sales are dying".

Both failure modes are equally expensive. Most tools ignore this side.

Detection logic
---------------
For each SKU, look at weekly sales buckets over the last 8+ weeks:

- Compute the average of the most recent N weeks vs the preceding N weeks
- If recent < prior × (1 - threshold), it's declining
- If this has been true for `consecutive_weeks` in a row, flag it

Additional heuristic: if days_of_cover > 180, mark as OVERSTOCK regardless
of trend (the warehouse is full of this stuff).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

from ..models import DeadStockAlert, InventorySnapshot, Product, SalesRecord


def _bucket_by_week(history: list[SalesRecord]) -> list[tuple[date, int]]:
    """Return list of (week_start_monday, total_units) sorted oldest to newest."""
    if not history:
        return []
    weekly: dict[date, int] = defaultdict(int)
    for rec in history:
        # Monday of the week
        monday = rec.date - timedelta(days=rec.date.weekday())
        weekly[monday] += rec.units_sold
    return sorted(weekly.items())


def detect_dead_stock(
    product: Product,
    history: list[SalesRecord],
    inventory: Optional[InventorySnapshot],
    decline_threshold: float = 0.35,
    consecutive_weeks_required: int = 4,
    min_history_weeks: int = 8,
    today: Optional[date] = None,
) -> Optional[DeadStockAlert]:
    """Check whether a SKU should be flagged as declining / dead / overstock.

    Returns DeadStockAlert if flagged, else None.
    """
    today = today or date.today()
    weekly = _bucket_by_week(history)

    # Overstock check (works even without history)
    current_stock = 0
    if inventory:
        current_stock = inventory.total_available

    # Trend-based dead stock detection
    if len(weekly) < min_history_weeks:
        # Not enough history — can only do overstock check
        if current_stock > 0 and weekly:
            recent_avg = mean(w[1] for w in weekly[-4:]) / 7 if weekly else 0
            if recent_avg > 0:
                days = current_stock / recent_avg
                if days > 180:
                    return DeadStockAlert(
                        sku=product.sku,
                        product_name=product.name,
                        weeks_declining=0,
                        decline_pct=0.0,
                        current_stock=current_stock,
                        days_to_deplete=round(days, 0),
                        suggested_action=f"📦 严重积压（{int(days)} 天库存），考虑清仓或打折促销",
                    )
        return None

    # Compare recent N weeks vs prior N weeks
    n = 4
    recent = [w[1] for w in weekly[-n:]]
    prior = [w[1] for w in weekly[-2 * n : -n]]
    if not prior or not recent:
        return None

    recent_avg = mean(recent)
    prior_avg = mean(prior)
    if prior_avg <= 0:
        return None

    decline_pct = (prior_avg - recent_avg) / prior_avg

    # Also check consecutive decline
    consecutive_decline = 0
    for i in range(len(weekly) - 1, 0, -1):
        if weekly[i][1] < weekly[i - 1][1]:
            consecutive_decline += 1
        else:
            break

    if decline_pct < decline_threshold:
        # Not declining enough; but still check overstock
        if recent_avg > 0 and current_stock > 0:
            days = current_stock / (recent_avg / 7)
            if days > 180:
                return DeadStockAlert(
                    sku=product.sku,
                    product_name=product.name,
                    weeks_declining=consecutive_decline,
                    decline_pct=round(decline_pct, 3),
                    current_stock=current_stock,
                    days_to_deplete=round(days, 0),
                    suggested_action=f"📦 积压严重（{int(days)} 天库存），不要再备货",
                )
        return None

    # Declining
    if recent_avg > 0 and current_stock > 0:
        days = current_stock / (recent_avg / 7)
    else:
        days = 999.0

    if consecutive_decline >= consecutive_weeks_required or decline_pct > 0.5:
        action = f"⚫ 销量连续 {consecutive_decline} 周下降 {int(decline_pct * 100)}%，建议清仓，**停止备货**"
    else:
        action = f"🟤 销量下降 {int(decline_pct * 100)}% (4周均值对比)，密切观察，暂缓大额备货"

    return DeadStockAlert(
        sku=product.sku,
        product_name=product.name,
        weeks_declining=consecutive_decline,
        decline_pct=round(decline_pct, 3),
        current_stock=current_stock,
        days_to_deplete=round(days, 0),
        suggested_action=action,
    )


def scan_all_dead_stock(
    products: list[Product],
    history_by_sku: dict[str, list[SalesRecord]],
    inventory_by_sku: dict[str, InventorySnapshot],
    **kwargs,
) -> list[DeadStockAlert]:
    """Scan all products for dead stock."""
    alerts: list[DeadStockAlert] = []
    for p in products:
        alert = detect_dead_stock(
            product=p,
            history=history_by_sku.get(p.sku, []),
            inventory=inventory_by_sku.get(p.sku),
            **kwargs,
        )
        if alert:
            alerts.append(alert)
    # Sort by severity: worst first (by decline_pct descending, then days_to_deplete descending)
    alerts.sort(key=lambda a: (-a.decline_pct, -a.days_to_deplete))
    return alerts
