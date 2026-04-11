"""End-to-end demo — runs the full pipeline against synthetic data, no Bitable / no Feishu.

Usage:
    python -m inventory_bot.demo

Prints:
    - Data quality summary
    - Forecast snippet
    - Reorder recommendations (sorted by urgency)
    - Dead stock alerts
    - Supplier learning profiles
    - PO generation result

This is the smoke test used both by the user (to see the system working)
and by CI to verify no module has a runtime error.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal

from .core.data_quality import summarize_issues, validate_all
from .core.dead_stock import scan_all_dead_stock
from .core.forecast_baseline import (
    generate_baseline_forecast,
    merge_with_manual_overrides,
)
from .core.reorder_engine import compute_all_recommendations, filter_by_status
from .core.supplier_learning import (
    compare_promised_vs_actual,
    learn_per_sku_lead_times,
    learn_supplier_lead_times,
)
from .workflows.po_generator import generate_draft_pos
from .tests.sample_data import build_sample_dataset


def _section(title: str) -> None:
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def _compact(obj) -> str:
    return str(obj).replace("  ", " ")[:200]


def main() -> int:
    data = build_sample_dataset()
    today = data["today"]
    products = data["products"]
    sales_history = data["sales_history"]
    inventory_latest = data["inventory_latest"]
    inventory_previous = data["inventory_previous"]
    pos = data["pos"]

    history_by_sku: dict[str, list] = {}
    for rec in sales_history:
        history_by_sku.setdefault(rec.sku, []).append(rec)

    pos_by_sku: dict[str, list] = {}
    for po in pos:
        pos_by_sku.setdefault(po.sku, []).append(po)

    # === 1. Data quality ===
    _section("1. 数据质量检查")
    issues = validate_all(
        products=products,
        history_by_sku=history_by_sku,
        current_inventory=inventory_latest,
        previous_inventory=inventory_previous,
        pos_by_sku=pos_by_sku,
        today=today,
    )
    summary = summarize_issues(issues)
    print(f"发现 {summary['total']} 条问题 | error={summary['error']} warning={summary['warning']} info={summary['info']}")
    for i in issues[:10]:
        print(f"  [{i.severity.upper()}] {i.sku} — {i.message}")
    if summary["error"]:
        print("\n🛑 存在错误级别的数据问题，真实运行会在此停止。")

    # === 2. Forecast baseline ===
    _section("2. 预测基线生成")
    all_forecasts: list = []
    for p in products:
        forecasts = generate_baseline_forecast(
            sku=p.sku,
            history=history_by_sku.get(p.sku, []),
            horizon_days=60,
            start_date=today,
            product_line=p.product_line,
        )
        all_forecasts.extend(forecasts)
    merged = merge_with_manual_overrides(all_forecasts, [])
    print(f"生成 {len(all_forecasts)} 条预测记录")
    print(f"样例 (GX-001 未来 7 天):")
    gx001_fc = [f for f in merged if f.sku == "GX-001"][:7]
    for f in gx001_fc:
        print(f"  {f.date} : {f.forecast_units} 件 ({f.source})")

    forecast_by_sku: dict[str, list] = {}
    for f in merged:
        forecast_by_sku.setdefault(f.sku, []).append(f)

    # === 3. Supplier learning ===
    _section("3. 供应商 lead time 学习")
    profiles = learn_supplier_lead_times(pos)
    for sup, prof in profiles.items():
        print(f"  {prof.to_note()}")
    print("\n促销 vs 实际兑现率 (slippage):")
    for row in compare_promised_vs_actual(products, pos)[:10]:
        print(
            f"  {row['verdict']} {row['sku']} @ {row['supplier']}: "
            f"承诺 {row['promised_days']} → 实际 {row['observed_mean_days']:.0f} "
            f"(slip {row['slip_days']:+.0f})"
        )

    # === 4. Reorder engine ===
    _section("4. 补货建议 (按紧急度排序)")
    recommendations = compute_all_recommendations(
        products=products,
        history_by_sku=history_by_sku,
        forecast_by_sku=forecast_by_sku,
        inventory_by_sku=inventory_latest,
        pos_by_sku=pos_by_sku,
        today=today,
    )
    header = f"{'SKU':<10} {'状态':<8} {'剩余':>6} {'可用':>8} {'建议量':>8} {'金额RMB':>12} {'下单':<12} {'到货':<12}"
    print(header)
    print("-" * len(header))
    for r in recommendations:
        val = f"¥{r.estimated_order_value_rmb:,.0f}" if r.estimated_order_value_rmb else "—"
        print(
            f"{r.sku:<10} {r.status:<8} "
            f"{r.days_of_cover_remaining:>6.0f} "
            f"{r.total_available:>8d} "
            f"{r.recommended_order_qty:>8d} "
            f"{val:>12} "
            f"{str(r.recommended_order_date):<12} "
            f"{str(r.expected_arrival_date):<12}"
        )
        for n in r.notes:
            print(f"             {n}")

    # === 5. Dead stock ===
    _section("5. 死库存 / 下滑扫描")
    dead_alerts = scan_all_dead_stock(
        products=products,
        history_by_sku=history_by_sku,
        inventory_by_sku=inventory_latest,
        today=today,
    )
    if not dead_alerts:
        print("(无死库存警报)")
    for a in dead_alerts:
        print(
            f"  {a.sku} ({a.product_name}): 下降 {int(a.decline_pct * 100)}% | "
            f"库存 {a.current_stock} | 够用 {a.days_to_deplete:.0f} 天 | "
            f"{a.suggested_action}"
        )

    # === 6. PO generation ===
    _section("6. 草稿 PO 生成")
    products_by_sku = {p.sku: p for p in products}
    po_result = generate_draft_pos(
        recommendations=recommendations,
        products_by_sku=products_by_sku,
        run_date=today,
    )
    print(f"生成 {len(po_result.drafts)} 张草稿 PO, 总金额 ¥{po_result.total_value_rmb:,.0f}")
    for d in po_result.drafts:
        print(
            f"  {d.po_number} | {d.sku} × {d.qty} | {d.supplier} | "
            f"{d.order_date} → {d.expected_arrival_date}"
        )
    if po_result.skipped:
        print(f"\n跳过 {len(po_result.skipped)} 个 SKU:")
        for sku, reason in po_result.skipped[:5]:
            print(f"  {sku}: {reason}")

    # === 7. Counts ===
    _section("7. 总览")
    urgent = filter_by_status(recommendations, "🔴急单")
    planned = filter_by_status(recommendations, "🟡计划")
    safe = filter_by_status(recommendations, "🟢安全")
    overstock = filter_by_status(recommendations, "📦积压")
    print(f"🔴 急单: {len(urgent)}")
    print(f"🟡 计划: {len(planned)}")
    print(f"🟢 安全: {len(safe)}")
    print(f"📦 积压: {len(overstock)}")
    print(f"🟤 死库存: {len(dead_alerts)}")
    print(f"\n本周需关注总金额: ¥{sum((r.estimated_order_value_rmb or Decimal(0)) for r in urgent + planned):,.0f}")
    print("\n✅ Demo 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
