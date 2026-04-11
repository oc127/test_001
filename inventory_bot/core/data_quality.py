"""Data quality validator — runs BEFORE reorder calculation.

Why this exists
---------------
Garbage-in-garbage-out is the #1 failure mode. An assistant typing "10"
instead of "1000" for in-stock makes the system scream "🔴 URGENT" and
could lead to a wrong order costing ¥50k.

This module runs a battery of sanity checks and returns a list of issues.
The workflow pipeline stops and asks the assistant to confirm/fix before
running the reorder engine.

Checks
------
1. Inventory magnitude swings (in_stock dropped > 80% from last week)
2. Sales spikes (today's sales > 10× recent average)
3. Sales zeros (missing data?)
4. Negative or None values that shouldn't exist
5. PO quantities vs product MOQ (did buyer forget MOQ?)
6. Date sanity (expected arrival before order date)
7. Duplicate records (same SKU × same date in sales history)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

from ..models import (
    DataQualityIssue,
    InventorySnapshot,
    Product,
    PurchaseOrder,
    SalesRecord,
)


def check_inventory_swing(
    sku: str,
    current: InventorySnapshot,
    previous: Optional[InventorySnapshot],
    threshold: float = 0.8,
) -> list[DataQualityIssue]:
    """Detect dramatic inventory changes suggesting typos."""
    if previous is None:
        return []
    issues: list[DataQualityIssue] = []
    for field in ("qty_in_stock", "qty_in_transit", "qty_in_production"):
        cur = getattr(current, field)
        prev = getattr(previous, field)
        if prev > 100 and cur >= 0:  # only check when we have a meaningful baseline
            change = (prev - cur) / prev if prev > 0 else 0
            if change > threshold:
                issues.append(
                    DataQualityIssue(
                        sku=sku,
                        severity="warning",
                        field=field,
                        message=(
                            f"{field} 从 {prev} 降到 {cur}（下降 {int(change * 100)}%）"
                        ),
                        suggested_action="请确认是否输错数字；或者是正常的大量消耗？",
                    )
                )
        # Negative values
        if cur < 0:
            issues.append(
                DataQualityIssue(
                    sku=sku,
                    severity="error",
                    field=field,
                    message=f"{field} 为负数 ({cur})",
                    suggested_action="库存不能为负，请修正",
                )
            )
    return issues


def check_sales_spike(
    sku: str,
    history: list[SalesRecord],
    lookback_days: int = 7,
    spike_multiplier: float = 10.0,
) -> list[DataQualityIssue]:
    """Detect sales spikes that look like data duplication."""
    if len(history) < lookback_days + 1:
        return []
    sorted_hist = sorted(history, key=lambda r: r.date)
    latest = sorted_hist[-1]
    recent = sorted_hist[-(lookback_days + 1) : -1]
    if not recent:
        return []
    recent_avg = mean(r.units_sold for r in recent)
    if recent_avg > 0 and latest.units_sold > recent_avg * spike_multiplier:
        return [
            DataQualityIssue(
                sku=sku,
                severity="warning",
                field="sales_history",
                message=(
                    f"{latest.date} 销量 {latest.units_sold} 是前 {lookback_days} 天均值 "
                    f"{recent_avg:.1f} 的 {latest.units_sold / recent_avg:.0f}× 倍"
                ),
                suggested_action="请检查是否数据重复或真实的爆单",
            )
        ]
    return []


def check_duplicate_sales(
    history: list[SalesRecord],
) -> list[DataQualityIssue]:
    """Detect duplicate (sku, date) rows."""
    issues: list[DataQualityIssue] = []
    seen: Counter[tuple[str, date]] = Counter()
    for rec in history:
        seen[(rec.sku, rec.date)] += 1
    for (sku, d), count in seen.items():
        if count > 1:
            issues.append(
                DataQualityIssue(
                    sku=sku,
                    severity="warning",
                    field="sales_history",
                    message=f"{d} 有 {count} 条销售记录",
                    suggested_action="请合并或删除重复行",
                )
            )
    return issues


def check_missing_recent_sales(
    sku: str,
    history: list[SalesRecord],
    expected_last_date: Optional[date] = None,
    max_gap_days: int = 7,
) -> list[DataQualityIssue]:
    """Warn if the most recent sales record is too old."""
    if not history:
        return [
            DataQualityIssue(
                sku=sku,
                severity="info",
                field="sales_history",
                message="没有销售历史",
                suggested_action="如果是新品可忽略；否则请导入销售数据",
            )
        ]
    latest = max(r.date for r in history)
    expected = expected_last_date or date.today()
    gap = (expected - latest).days
    if gap > max_gap_days:
        return [
            DataQualityIssue(
                sku=sku,
                severity="warning",
                field="sales_history",
                message=f"最新销售记录是 {latest}，距今 {gap} 天",
                suggested_action="本周销售数据是否漏录？",
            )
        ]
    return []


def check_po_sanity(
    product: Product,
    pos: list[PurchaseOrder],
) -> list[DataQualityIssue]:
    """Validate PO dates and quantities."""
    issues: list[DataQualityIssue] = []
    for po in pos:
        if po.qty < product.moq and po.status == "draft":
            issues.append(
                DataQualityIssue(
                    sku=product.sku,
                    severity="warning",
                    field="purchase_orders",
                    message=f"PO {po.po_number} 数量 {po.qty} 低于 MOQ {product.moq}",
                    suggested_action="确认工厂是否接受低于 MOQ 的订单",
                )
            )
        if po.expected_arrival_date and po.order_date and po.expected_arrival_date < po.order_date:
            issues.append(
                DataQualityIssue(
                    sku=product.sku,
                    severity="error",
                    field="purchase_orders",
                    message=f"PO {po.po_number} 预计到货日期早于下单日期",
                    suggested_action="请修正日期",
                )
            )
        if po.actual_arrival_date and po.order_date and po.actual_arrival_date < po.order_date:
            issues.append(
                DataQualityIssue(
                    sku=product.sku,
                    severity="error",
                    field="purchase_orders",
                    message=f"PO {po.po_number} 实际到货日期早于下单日期",
                    suggested_action="请修正日期",
                )
            )
    return issues


def validate_all(
    products: list[Product],
    history_by_sku: dict[str, list[SalesRecord]],
    current_inventory: dict[str, InventorySnapshot],
    previous_inventory: dict[str, InventorySnapshot],
    pos_by_sku: dict[str, list[PurchaseOrder]],
    today: Optional[date] = None,
) -> list[DataQualityIssue]:
    """Run all checks across all products. Returns aggregated issues."""
    issues: list[DataQualityIssue] = []
    today = today or date.today()

    # Global duplicate check
    all_history: list[SalesRecord] = []
    for lst in history_by_sku.values():
        all_history.extend(lst)
    issues.extend(check_duplicate_sales(all_history))

    for product in products:
        sku = product.sku
        history = history_by_sku.get(sku, [])

        # Inventory swings
        if sku in current_inventory:
            issues.extend(
                check_inventory_swing(
                    sku=sku,
                    current=current_inventory[sku],
                    previous=previous_inventory.get(sku),
                )
            )

        # Sales spikes
        issues.extend(check_sales_spike(sku, history))

        # Missing recent sales (lenient for 新品)
        issues.extend(
            check_missing_recent_sales(
                sku=sku,
                history=history,
                expected_last_date=today,
                max_gap_days=7,
            )
        )

        # PO sanity
        issues.extend(check_po_sanity(product, pos_by_sku.get(sku, [])))

    return issues


def summarize_issues(issues: list[DataQualityIssue]) -> dict:
    """Return counts by severity for reporting."""
    counts: dict[str, int] = defaultdict(int)
    for issue in issues:
        counts[issue.severity] += 1
    return {
        "total": len(issues),
        "error": counts["error"],
        "warning": counts["warning"],
        "info": counts["info"],
    }
