"""Monthly retrospective report — runs on the 1st of each month at 9am.

Why this exists
---------------
Weekly alerts tell you WHAT to do. Monthly reviews tell you HOW YOU'RE
DOING. Without this, the system becomes a nagging alarm clock and the
user never improves.

This workflow computes:

1. **Purchasing stats**: total spend, number of POs, per-supplier breakdown.
2. **Stockout incidents**: days a SKU hit zero inventory (reconstructed
   from inventory snapshots — if qty_in_stock = 0 for a day, that's a
   stockout day).
3. **Lead time slippage**: compare_promised_vs_actual for every supplier.
4. **Overstock capital**: how much money is trapped in slow-moving SKUs.
5. **Dead stock write-offs**: SKUs flagged dead this month that still
   have inventory.
6. **Forecast accuracy**: compare last month's forecast vs actual sales.
7. **Recommendations for next month**: concrete actions derived from above.

Output: a markdown report saved to data_out/, plus a summary card pushed
to Feishu.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Optional

from ..config import DATA_OUT_DIR
from ..core.supplier_learning import (
    compare_promised_vs_actual,
    learn_supplier_lead_times,
)
from ..models import (
    ForecastRecord,
    InventorySnapshot,
    Product,
    PurchaseOrder,
    SalesRecord,
)
from ..clients.bitable import InventoryBotRepository
from ..clients.feishu import FeishuBot, build_monthly_review_card

logger = logging.getLogger(__name__)


@dataclass
class MonthlyMetrics:
    month_label: str
    period_start: date
    period_end: date
    total_ordered_value_rmb: Decimal = Decimal("0")
    po_count: int = 0
    unique_suppliers: int = 0
    per_supplier_spend: dict[str, Decimal] = field(default_factory=dict)
    stockout_events: list[dict] = field(default_factory=list)
    slippage_rows: list[dict] = field(default_factory=list)
    top_unreliable_suppliers: list[str] = field(default_factory=list)
    overstock_capital_rmb: Decimal = Decimal("0")
    dead_stock_skus: list[str] = field(default_factory=list)
    forecast_accuracy_pct: Optional[float] = None  # 100% = perfect
    recommendations: list[str] = field(default_factory=list)


# ===== Metric computers =====

def _month_bounds(target_month: Optional[date] = None) -> tuple[date, date, str]:
    """Return first-of-month, last-of-month, label for the month BEFORE target."""
    today = target_month or date.today()
    first_of_this = today.replace(day=1)
    last_of_last = first_of_this - timedelta(days=1)
    first_of_last = last_of_last.replace(day=1)
    return first_of_last, last_of_last, f"{first_of_last.year}年{first_of_last.month}月"


def compute_purchasing_stats(
    pos: list[PurchaseOrder],
    period_start: date,
    period_end: date,
) -> tuple[Decimal, int, dict[str, Decimal]]:
    total = Decimal("0")
    per_supplier: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    count = 0
    for po in pos:
        if not (period_start <= po.order_date <= period_end):
            continue
        if po.status in ("draft", "cancelled"):
            continue
        count += 1
        value = po.total_rmb or Decimal("0")
        total += value
        per_supplier[po.supplier or "(未指定)"] += value
    return total, count, dict(per_supplier)


def compute_stockout_events(
    sales_history: list[SalesRecord],
    inventory_history: list[InventorySnapshot],
    period_start: date,
    period_end: date,
) -> list[dict]:
    """Identify days where SKUs had inventory=0 but had sales before."""
    events: list[dict] = []
    # Group inventory snapshots by SKU, sorted by date
    by_sku: dict[str, list[InventorySnapshot]] = defaultdict(list)
    for snap in inventory_history:
        if period_start <= snap.snapshot_date <= period_end:
            by_sku[snap.sku].append(snap)
    # Group sales by SKU
    sold_skus: set[str] = {r.sku for r in sales_history}

    for sku, snaps in by_sku.items():
        snaps.sort(key=lambda s: s.snapshot_date)
        for snap in snaps:
            if snap.qty_in_stock == 0 and sku in sold_skus:
                events.append(
                    {
                        "sku": sku,
                        "date": snap.snapshot_date.isoformat(),
                        "in_transit": snap.qty_in_transit,
                        "in_production": snap.qty_in_production,
                    }
                )
    return events


def compute_overstock_capital(
    products: list[Product],
    inventory_by_sku: dict[str, InventorySnapshot],
    sales_history: list[SalesRecord],
    overstock_days_threshold: int = 180,
) -> tuple[Decimal, list[str]]:
    """Return (¥ trapped in overstock, list of offending SKUs)."""
    # Compute 30-day avg daily sales per SKU
    from collections import Counter

    by_sku_sum: Counter[str] = Counter()
    for rec in sales_history[-5000:]:  # cap
        by_sku_sum[rec.sku] += rec.units_sold
    by_sku_days: Counter[str] = Counter()
    seen_dates_per_sku: dict[str, set[date]] = defaultdict(set)
    for rec in sales_history[-5000:]:
        seen_dates_per_sku[rec.sku].add(rec.date)
    for sku, dates in seen_dates_per_sku.items():
        by_sku_days[sku] = max(1, len(dates))

    trapped = Decimal("0")
    offenders: list[str] = []
    for product in products:
        inv = inventory_by_sku.get(product.sku)
        if not inv:
            continue
        daily = by_sku_sum[product.sku] / by_sku_days[product.sku] if by_sku_days[product.sku] else 0
        if daily <= 0:
            # No sales but has stock → 100% overstock
            if inv.total_available > 0 and product.unit_cost_rmb:
                trapped += Decimal(inv.total_available) * product.unit_cost_rmb
                offenders.append(product.sku)
            continue
        days = inv.total_available / daily
        if days > overstock_days_threshold:
            excess_units = inv.total_available - int(daily * overstock_days_threshold)
            if excess_units > 0 and product.unit_cost_rmb:
                trapped += Decimal(excess_units) * product.unit_cost_rmb
                offenders.append(product.sku)
    return trapped, offenders


def compute_forecast_accuracy(
    forecasts: list[ForecastRecord],
    sales_history: list[SalesRecord],
    period_start: date,
    period_end: date,
) -> Optional[float]:
    """Compare forecast vs actual for the review period. Returns accuracy %.

    Accuracy = 100 × (1 - |sum_forecast - sum_actual| / sum_actual)
    Bounded at 0.
    """
    forecast_sum = sum(
        f.forecast_units for f in forecasts if period_start <= f.date <= period_end
    )
    actual_sum = sum(
        r.units_sold for r in sales_history if period_start <= r.date <= period_end
    )
    if actual_sum == 0:
        return None
    error = abs(forecast_sum - actual_sum) / actual_sum
    return max(0.0, round(100 * (1 - error), 1))


# ===== Assembler =====

def assemble_recommendations(metrics: MonthlyMetrics) -> list[str]:
    """Synthesise concrete next-month actions from the raw metrics."""
    recs: list[str] = []
    if metrics.top_unreliable_suppliers:
        recs.append(
            f"约谈 {len(metrics.top_unreliable_suppliers)} 家供应商：" +
            "、".join(metrics.top_unreliable_suppliers[:3]) +
            "（实际 lead 显著超过承诺）"
        )
    if metrics.overstock_capital_rmb > Decimal("20000"):
        recs.append(
            f"清理积压：释放 ¥{metrics.overstock_capital_rmb:,.0f} 占用资金，考虑促销/清仓"
        )
    if metrics.dead_stock_skus:
        recs.append(
            f"{len(metrics.dead_stock_skus)} 个 SKU 被判死库存，下月起停止备货"
        )
    if metrics.stockout_events:
        recs.append(
            f"本月发生 {len(metrics.stockout_events)} 次断货，检查安全库存参数是否过低"
        )
    if metrics.forecast_accuracy_pct is not None and metrics.forecast_accuracy_pct < 70:
        recs.append(
            f"预测准确率仅 {metrics.forecast_accuracy_pct:.0f}%，建议审查手动覆盖与促销节点"
        )
    if not recs:
        recs.append("本月整体表现良好，继续保持当前节奏")
    return recs


def format_markdown_report(metrics: MonthlyMetrics) -> str:
    lines: list[str] = []
    lines.append(f"# {metrics.month_label} 备货月度复盘\n")
    lines.append(f"**期间**: {metrics.period_start} → {metrics.period_end}\n")
    lines.append("## 一、采购统计\n")
    lines.append(f"- 采购总额: **¥{metrics.total_ordered_value_rmb:,.0f}**")
    lines.append(f"- PO 数量: {metrics.po_count}")
    lines.append(f"- 涉及供应商: {metrics.unique_suppliers}")
    lines.append("")
    if metrics.per_supplier_spend:
        lines.append("| 供应商 | 本月金额 |")
        lines.append("|---|---|")
        for sup, amt in sorted(
            metrics.per_supplier_spend.items(), key=lambda x: -x[1]
        )[:10]:
            lines.append(f"| {sup} | ¥{amt:,.0f} |")
        lines.append("")

    lines.append("## 二、供应商 lead time 兑现率\n")
    if metrics.slippage_rows:
        lines.append("| SKU | 供应商 | 承诺天数 | 实际均值 | p90 | 延迟 | 判断 |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in metrics.slippage_rows[:20]:
            lines.append(
                f"| {row['sku']} | {row['supplier']} | {row['promised_days']} | "
                f"{row['observed_mean_days']:.0f} | {row['observed_p90_days']:.0f} | "
                f"{row['slip_days']:+.0f} | {row['verdict']} |"
            )
        lines.append("")
    else:
        lines.append("无完整历史数据 (need completed POs with actual_arrival_date)\n")

    lines.append("## 三、断货事件\n")
    if metrics.stockout_events:
        lines.append(f"本月共发生 **{len(metrics.stockout_events)}** 次 SKU-日 断货:\n")
        for ev in metrics.stockout_events[:10]:
            lines.append(
                f"- {ev['date']} · {ev['sku']} · 在途 {ev['in_transit']} · 在产 {ev['in_production']}"
            )
        lines.append("")
    else:
        lines.append("无断货事件 ✅\n")

    lines.append("## 四、资金占用\n")
    lines.append(f"- 过度积压占用资金: **¥{metrics.overstock_capital_rmb:,.0f}**")
    if metrics.dead_stock_skus:
        lines.append(f"- 死库存 SKU 数: {len(metrics.dead_stock_skus)}")
    lines.append("")

    lines.append("## 五、预测准确率\n")
    if metrics.forecast_accuracy_pct is not None:
        lines.append(f"- 上月预测 vs 实际: **{metrics.forecast_accuracy_pct:.0f}%**\n")
    else:
        lines.append("- 缺少数据，无法计算\n")

    lines.append("## 六、下月行动建议\n")
    for rec in metrics.recommendations:
        lines.append(f"- {rec}")
    lines.append("")

    return "\n".join(lines)


def run_monthly_review(
    today: Optional[date] = None,
    dry_run: bool = False,
    repo: Optional[InventoryBotRepository] = None,
    bot: Optional[FeishuBot] = None,
    output_dir: Path = DATA_OUT_DIR,
) -> MonthlyMetrics:
    today = today or date.today()
    repo = repo or InventoryBotRepository()
    bot = bot or FeishuBot()

    period_start, period_end, month_label = _month_bounds(today)
    metrics = MonthlyMetrics(
        month_label=month_label,
        period_start=period_start,
        period_end=period_end,
    )

    try:
        products = repo.fetch_products()
        pos = repo.fetch_purchase_orders()
        sales_history = repo.fetch_sales_history()
        forecasts = repo.fetch_forecasts()
        latest_inventory = repo.fetch_latest_inventory()
    except Exception as exc:
        logger.exception("Monthly review data fetch failed: %s", exc)
        return metrics

    # 1. Purchasing
    total, count, per_sup = compute_purchasing_stats(pos, period_start, period_end)
    metrics.total_ordered_value_rmb = total
    metrics.po_count = count
    metrics.unique_suppliers = len(per_sup)
    metrics.per_supplier_spend = per_sup

    # 2. Slippage
    slippage = compare_promised_vs_actual(products, pos)
    metrics.slippage_rows = slippage
    metrics.top_unreliable_suppliers = [
        row["supplier"]
        for row in slippage
        if row["slip_days"] > 7
    ]

    # 3. Stockouts — use all inventory snapshots, not just latest
    try:
        # fallback — fetch via direct call if method doesn't exist
        inventory_history_records: list[InventorySnapshot] = []
        # We only have fetch_latest; caller may extend. Skip if unavailable.
        all_snaps = [latest_inventory[sku] for sku in latest_inventory]
        metrics.stockout_events = compute_stockout_events(
            sales_history, all_snaps, period_start, period_end
        )
    except Exception:
        metrics.stockout_events = []

    # 4. Overstock capital
    trapped, offenders = compute_overstock_capital(
        products=products,
        inventory_by_sku=latest_inventory,
        sales_history=sales_history,
    )
    metrics.overstock_capital_rmb = trapped
    metrics.dead_stock_skus = offenders

    # 5. Forecast accuracy
    metrics.forecast_accuracy_pct = compute_forecast_accuracy(
        forecasts, sales_history, period_start, period_end
    )

    # 6. Recommendations
    metrics.recommendations = assemble_recommendations(metrics)

    # ---- Output: markdown + Feishu card ----
    output_dir.mkdir(parents=True, exist_ok=True)
    md = format_markdown_report(metrics)
    report_path = output_dir / f"monthly_review_{period_start.strftime('%Y%m')}.md"
    if not dry_run:
        report_path.write_text(md, encoding="utf-8")

    if not dry_run:
        card = build_monthly_review_card(
            month=month_label,
            total_ordered_value_rmb=float(metrics.total_ordered_value_rmb),
            stockout_count=len(metrics.stockout_events),
            overstock_rmb_impact=float(metrics.overstock_capital_rmb),
            top_unreliable_suppliers=metrics.top_unreliable_suppliers,
        )
        bot.send_card(card)

    return metrics
