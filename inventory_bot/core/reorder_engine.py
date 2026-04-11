"""Core reorder-point engine.

This is the heart of the system. Given:
- A product's lead times and safety stock policy
- Its sales history
- Its sales forecast
- Its current inventory (in stock + in transit + in production)
- Its pending purchase orders

...it computes whether to reorder NOW, PLAN for reorder, or SAFE to do nothing,
and calculates HOW MUCH to order.

Core formula
------------
Adjusted lead time = base lead time + holiday extension (CNY/National Day/etc.)

Demand in lead time = sum of forecast_units for dates [today, today + adjusted_lead_days]

Safety stock = avg_daily_sales × safety_stock_days
             × peak_season_multiplier(arrival_date)
             + z_score × std_daily_sales × sqrt(adjusted_lead_days)
  (z_score = 1.65 ≈ 95% service level)

Reorder point (ROP) = demand_in_lead_time + safety_stock

Available = in_stock + in_transit + in_production + sum(qty of pending POs)

Status:
  - 🔴 URGENT   if available < ROP
  - 🟡 PLANNED  if available < ROP + 7-day buffer
  - 🟢 SAFE     otherwise
  - 🟤 DEAD     if dead_stock_detector flags it
  - 📦 OVER     if coverage > 180 days

Recommended order quantity = demand_in_horizon (60 days) - available + safety_stock
  aligned up to product MOQ.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..config import PRODUCT_LINE_DEFAULTS, ReorderStatus
from ..models import (
    ForecastRecord,
    InventorySnapshot,
    Product,
    PurchaseOrder,
    ReorderRecommendation,
    SalesRecord,
)
from .forecast_baseline import daily_sales_stats, trailing_average
from .holiday_calendar import adjust_lead_time, amazon_peak_multiplier


# Service level z-score — 1.65 = 95%, 1.28 = 90%, 2.33 = 99%
DEFAULT_Z_SCORE = 1.65

# Order horizon — when we decide to order, we order enough for the next N days
# beyond lead time. 60 days is a reasonable mid-range.
ORDER_HORIZON_DAYS = 60


def _sum_forecast(
    forecasts: list[ForecastRecord],
    start: date,
    end: date,
) -> int:
    """Sum forecast units in [start, end]."""
    return sum(f.forecast_units for f in forecasts if start <= f.date <= end)


def _active_po_qty(pos: list[PurchaseOrder]) -> int:
    """Sum quantities of POs that are contributing to future stock."""
    return sum(p.qty for p in pos if p.is_active)


def _round_up_to_moq(qty: int, moq: int) -> int:
    if moq <= 0 or qty <= 0:
        return max(0, qty)
    return math.ceil(qty / moq) * moq


def compute_recommendation(
    product: Product,
    history: list[SalesRecord],
    forecasts: list[ForecastRecord],
    inventory: Optional[InventorySnapshot],
    pending_pos: list[PurchaseOrder],
    today: Optional[date] = None,
    z_score: float = DEFAULT_Z_SCORE,
    order_horizon_days: int = ORDER_HORIZON_DAYS,
) -> ReorderRecommendation:
    """Compute the reorder recommendation for a single SKU."""
    today = today or date.today()

    # 1. Demand stats from history
    avg_daily, std_daily = daily_sales_stats(history, window_days=30)
    if avg_daily <= 0:
        avg_daily = trailing_average(history, window_days=90)  # fallback longer window

    # 2. Lead time — adjust for holidays
    lt_adjust = adjust_lead_time(
        base_lead_days=product.total_lead_days,
        order_date=today,
        product_line=product.product_line,
        cny_extension_days=PRODUCT_LINE_DEFAULTS.get(
            product.product_line, {}
        ).get("chinese_new_year_extension_days", 30),
    )
    adjusted_lead_days = lt_adjust.adjusted_lead_days

    # 3. Demand within lead time (from forecast, not from avg)
    lt_end = today + timedelta(days=adjusted_lead_days)
    demand_in_lead_time = _sum_forecast(forecasts, today, lt_end)
    # Fallback if no forecast: use avg_daily × lead_days
    if demand_in_lead_time == 0 and avg_daily > 0:
        demand_in_lead_time = round(avg_daily * adjusted_lead_days)

    # 4. Demand within order horizon (what to order enough for)
    horizon_end = today + timedelta(days=order_horizon_days)
    demand_in_horizon = _sum_forecast(forecasts, today, horizon_end)
    if demand_in_horizon == 0 and avg_daily > 0:
        demand_in_horizon = round(avg_daily * order_horizon_days)

    # 5. Safety stock
    peak_mult, peak_reason = amazon_peak_multiplier(lt_end)
    base_safety = avg_daily * product.safety_stock_days * peak_mult
    # Add service level buffer
    service_buffer = z_score * std_daily * math.sqrt(max(1, adjusted_lead_days))
    safety_stock = round(base_safety + service_buffer)

    # 6. Available inventory
    in_stock = inventory.qty_in_stock if inventory else 0
    in_transit = inventory.qty_in_transit if inventory else 0
    in_production = inventory.qty_in_production if inventory else 0
    pending_qty = _active_po_qty(pending_pos)
    total_available = in_stock + in_transit + in_production + pending_qty

    # 7. Reorder point
    reorder_point = demand_in_lead_time + safety_stock
    coverage_gap = total_available - reorder_point
    days_of_cover = (total_available / avg_daily) if avg_daily > 0 else 999

    # 8. Status
    planned_buffer = avg_daily * 7  # one extra week of buffer = PLANNED
    if coverage_gap < 0:
        status = ReorderStatus.URGENT
    elif coverage_gap < planned_buffer:
        status = ReorderStatus.PLANNED
    elif days_of_cover > 180:
        status = ReorderStatus.OVERSTOCK
    else:
        status = ReorderStatus.SAFE

    # 9. Recommended order quantity
    # Order enough for the horizon minus what we already have
    raw_order = demand_in_horizon - total_available + safety_stock
    raw_order = max(0, raw_order)
    recommended_qty = _round_up_to_moq(raw_order, product.moq)

    # 10. Recommended order date
    if status == ReorderStatus.URGENT:
        recommended_order_date = today
    elif status == ReorderStatus.PLANNED:
        # Calculate when we'd run out after safety stock
        days_until_action = int(coverage_gap / avg_daily) if avg_daily > 0 else 7
        recommended_order_date = today + timedelta(days=max(0, days_until_action - 3))
    else:
        recommended_order_date = today + timedelta(days=30)  # Placeholder

    # 11. Expected arrival
    expected_arrival = recommended_order_date + timedelta(days=adjusted_lead_days)

    # 12. Urgency score — for sorting. Lower = more urgent.
    # Score = days_of_cover - lead_time. Negative = already short.
    urgency_score = days_of_cover - adjusted_lead_days

    # 13. Estimated order value
    est_value = None
    if product.unit_cost_rmb is not None and recommended_qty > 0:
        est_value = Decimal(recommended_qty) * product.unit_cost_rmb

    # 14. Human-readable notes
    notes: list[str] = []
    if lt_adjust.is_adjusted:
        notes.append(f"⚠️ {lt_adjust.reason}")
    if peak_mult > 1.0:
        notes.append(f"📈 {peak_reason}")
    if pending_qty > 0:
        notes.append(f"ℹ️ 已有 {pending_qty} 件在途/在产 PO")
    if avg_daily < 0.5:
        notes.append("⚠️ 销量低于 0.5/天，数据可能不可靠")

    return ReorderRecommendation(
        sku=product.sku,
        product_name=product.name,
        product_line=product.product_line,
        status=status,
        current_in_stock=in_stock,
        current_in_transit=in_transit,
        current_in_production=in_production,
        pending_po_qty=pending_qty,
        total_available=total_available,
        avg_daily_sales=round(avg_daily, 2),
        std_daily_sales=round(std_daily, 2),
        demand_in_lead_time=demand_in_lead_time,
        demand_in_horizon=demand_in_horizon,
        days_of_cover_remaining=round(days_of_cover, 1),
        base_lead_days=lt_adjust.base_lead_days,
        adjusted_lead_days=adjusted_lead_days,
        lead_time_adjustment_reason=lt_adjust.reason,
        reorder_point=reorder_point,
        coverage_gap=coverage_gap,
        recommended_order_qty=recommended_qty,
        recommended_order_date=recommended_order_date,
        expected_arrival_date=expected_arrival,
        estimated_order_value_rmb=est_value,
        urgency_score=round(urgency_score, 2),
        notes=notes,
    )


def compute_all_recommendations(
    products: list[Product],
    history_by_sku: dict[str, list[SalesRecord]],
    forecast_by_sku: dict[str, list[ForecastRecord]],
    inventory_by_sku: dict[str, InventorySnapshot],
    pos_by_sku: dict[str, list[PurchaseOrder]],
    today: Optional[date] = None,
) -> list[ReorderRecommendation]:
    """Batch compute for all products, sorted by urgency (most urgent first)."""
    results: list[ReorderRecommendation] = []
    for product in products:
        rec = compute_recommendation(
            product=product,
            history=history_by_sku.get(product.sku, []),
            forecasts=forecast_by_sku.get(product.sku, []),
            inventory=inventory_by_sku.get(product.sku),
            pending_pos=pos_by_sku.get(product.sku, []),
            today=today,
        )
        results.append(rec)

    results.sort(key=lambda r: (r.urgency_score, r.sku))
    return results


def filter_by_status(
    recommendations: list[ReorderRecommendation],
    status: str,
) -> list[ReorderRecommendation]:
    return [r for r in recommendations if r.status == status]
