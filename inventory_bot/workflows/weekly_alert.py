"""Weekly reorder alert workflow — runs every Monday 9am.

End-to-end pipeline
-------------------
1. Pull fresh data from Bitable:
   - products, sales history, forecasts, latest inventory, previous inventory, POs
2. Run data quality checks. If errors found → emit a DQ card and STOP.
3. Generate baseline forecast for SKUs with no manual overrides → write back.
4. Learn supplier lead times from historical POs.
5. Run reorder engine for all products.
6. Run dead stock detection.
7. Write results to the decisions log table in Bitable.
8. Push summary card to Feishu bot.
9. For top N urgent SKUs, push individual detail cards.
10. Return a `WeeklyRunReport` summary.

The workflow is idempotent: running twice in a day writes two rows in the
decisions log but doesn't create duplicate POs (POs only created by
po_generator on explicit buyer approval).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from ..config import SETTINGS, ReorderStatus
from ..core.data_quality import summarize_issues, validate_all
from ..core.dead_stock import scan_all_dead_stock
from ..core.forecast_baseline import generate_baseline_forecast, merge_with_manual_overrides
from ..core.reorder_engine import compute_all_recommendations, filter_by_status
from ..core.supplier_learning import (
    learn_per_sku_lead_times,
    learn_supplier_lead_times,
)
from ..models import (
    DataQualityIssue,
    DeadStockAlert,
    ForecastRecord,
    ReorderRecommendation,
)
from ..clients.bitable import InventoryBotRepository
from ..clients.feishu import (
    FeishuBot,
    build_data_quality_card,
    build_urgent_sku_card,
    build_weekly_alert_card,
)

logger = logging.getLogger(__name__)


@dataclass
class WeeklyRunReport:
    run_at: datetime
    stopped_due_to_data_quality: bool = False
    data_quality_issues: list[DataQualityIssue] = field(default_factory=list)
    recommendations: list[ReorderRecommendation] = field(default_factory=list)
    dead_stock_alerts: list[DeadStockAlert] = field(default_factory=list)
    forecasts_generated: int = 0
    urgent_count: int = 0
    planned_count: int = 0
    safe_count: int = 0
    overstock_count: int = 0
    dead_count: int = 0
    total_urgent_value_rmb: float = 0.0
    feishu_sent: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "run_at": self.run_at.isoformat(),
            "stopped_due_to_data_quality": self.stopped_due_to_data_quality,
            "data_quality_issues": len(self.data_quality_issues),
            "forecasts_generated": self.forecasts_generated,
            "urgent_count": self.urgent_count,
            "planned_count": self.planned_count,
            "safe_count": self.safe_count,
            "overstock_count": self.overstock_count,
            "dead_count": self.dead_count,
            "total_urgent_value_rmb": self.total_urgent_value_rmb,
            "feishu_sent": self.feishu_sent,
            "error": self.error,
        }


def run_weekly_alert(
    today: Optional[date] = None,
    dry_run: bool = False,
    repo: Optional[InventoryBotRepository] = None,
    bot: Optional[FeishuBot] = None,
    write_back_forecasts: bool = True,
    max_urgent_detail_cards: int = 5,
) -> WeeklyRunReport:
    """Run the full weekly pipeline.

    Args:
        today: override for test/backfill. Defaults to today.
        dry_run: if True, no writes to Bitable, no Feishu push.
        repo: injectable for testing.
        bot: injectable for testing.
        write_back_forecasts: whether to persist generated baselines.
        max_urgent_detail_cards: how many individual cards to push after summary.
    """
    today = today or date.today()
    report = WeeklyRunReport(run_at=datetime.now())
    repo = repo or InventoryBotRepository()
    bot = bot or FeishuBot()

    try:
        # ---- 1. Load ----
        logger.info("Loading data from Bitable…")
        products = repo.fetch_products()
        sales_history = repo.fetch_sales_history()
        existing_forecasts = repo.fetch_forecasts()
        latest_inventory = repo.fetch_latest_inventory()
        previous_inventory = repo.fetch_previous_inventory(latest_inventory)
        pos = repo.fetch_purchase_orders()
        logger.info(
            "Loaded: %d products, %d sales, %d forecasts, %d inventory, %d POs",
            len(products),
            len(sales_history),
            len(existing_forecasts),
            len(latest_inventory),
            len(pos),
        )

        history_by_sku: dict[str, list] = {}
        for rec in sales_history:
            history_by_sku.setdefault(rec.sku, []).append(rec)

        pos_by_sku: dict[str, list] = {}
        for po in pos:
            pos_by_sku.setdefault(po.sku, []).append(po)

        # ---- 2. Data quality ----
        logger.info("Running data quality checks…")
        issues = validate_all(
            products=products,
            history_by_sku=history_by_sku,
            current_inventory=latest_inventory,
            previous_inventory=previous_inventory,
            pos_by_sku=pos_by_sku,
            today=today,
        )
        report.data_quality_issues = issues
        summary = summarize_issues(issues)
        logger.info("DQ summary: %s", summary)

        if summary["error"] > 0:
            report.stopped_due_to_data_quality = True
            card = build_data_quality_card(
                error_count=summary["error"],
                warning_count=summary["warning"],
                info_count=summary["info"],
                top_issues=[f"{i.sku}: {i.message}" for i in issues if i.severity == "error"],
            )
            if not dry_run:
                bot.send_card(card)
                report.feishu_sent = True
            return report

        # ---- 3. Forecast baseline ----
        logger.info("Generating baseline forecasts…")
        baseline_forecasts: list[ForecastRecord] = []
        for product in products:
            baseline_forecasts.extend(
                generate_baseline_forecast(
                    sku=product.sku,
                    history=history_by_sku.get(product.sku, []),
                    horizon_days=SETTINGS.forecast_horizon_days,
                    start_date=today,
                    product_line=product.product_line,
                )
            )
        # Keep any manual overrides the team typed in
        manual_overrides = [f for f in existing_forecasts if f.source in ("manual", "promotion")]
        merged = merge_with_manual_overrides(baseline_forecasts, manual_overrides)
        report.forecasts_generated = len(baseline_forecasts)

        if write_back_forecasts and not dry_run:
            repo.write_forecasts(merged)

        forecast_by_sku: dict[str, list[ForecastRecord]] = {}
        for f in merged:
            forecast_by_sku.setdefault(f.sku, []).append(f)

        # ---- 4. Supplier learning ----
        logger.info("Learning supplier lead times…")
        per_supplier = learn_supplier_lead_times(pos)
        per_sku = learn_per_sku_lead_times(pos)
        # Note: we currently pass nominal lead times to the engine. A more
        # advanced iteration would use resolve_effective_lead_days to inject
        # observed values into Product copies before calling the engine.

        # ---- 5. Reorder engine ----
        logger.info("Computing reorder recommendations…")
        recommendations = compute_all_recommendations(
            products=products,
            history_by_sku=history_by_sku,
            forecast_by_sku=forecast_by_sku,
            inventory_by_sku=latest_inventory,
            pos_by_sku=pos_by_sku,
            today=today,
        )
        report.recommendations = recommendations

        # ---- 6. Dead stock ----
        logger.info("Scanning dead stock…")
        dead_alerts = scan_all_dead_stock(
            products=products,
            history_by_sku=history_by_sku,
            inventory_by_sku=latest_inventory,
            decline_threshold=SETTINGS.dead_stock_decline_threshold,
            consecutive_weeks_required=SETTINGS.dead_stock_consecutive_weeks,
            today=today,
        )
        report.dead_stock_alerts = dead_alerts
        dead_skus = {a.sku for a in dead_alerts}

        # Tag dead-stock SKUs in the recommendations
        for rec in recommendations:
            if rec.sku in dead_skus:
                rec.status = ReorderStatus.DEAD_STOCK
                rec.recommended_order_qty = 0
                rec.notes.append("⚫ 检测为死库存/下滑，已清空建议订购量")

        # ---- 7. Counts ----
        urgent_list = filter_by_status(recommendations, ReorderStatus.URGENT)
        planned_list = filter_by_status(recommendations, ReorderStatus.PLANNED)
        safe_list = filter_by_status(recommendations, ReorderStatus.SAFE)
        overstock_list = filter_by_status(recommendations, ReorderStatus.OVERSTOCK)
        dead_list = filter_by_status(recommendations, ReorderStatus.DEAD_STOCK)

        report.urgent_count = len(urgent_list)
        report.planned_count = len(planned_list)
        report.safe_count = len(safe_list)
        report.overstock_count = len(overstock_list)
        report.dead_count = len(dead_list)
        report.total_urgent_value_rmb = float(
            sum(
                (r.estimated_order_value_rmb or Decimal("0"))
                for r in urgent_list
            )
        )

        # ---- 8. Write decisions back to Bitable ----
        logger.info("Writing decisions log to Bitable…")
        decision_rows = [
            {
                "sku": r.sku,
                "product_name": r.product_name,
                "status": r.status,
                "days_of_cover_remaining": r.days_of_cover_remaining,
                "total_available": r.total_available,
                "recommended_order_qty": r.recommended_order_qty,
                "recommended_order_date": r.recommended_order_date,
                "expected_arrival_date": r.expected_arrival_date,
                "estimated_order_value_rmb": (
                    float(r.estimated_order_value_rmb)
                    if r.estimated_order_value_rmb is not None
                    else None
                ),
                "urgency_score": r.urgency_score,
                "notes": "; ".join(r.notes),
                "decision": "待审核",
            }
            for r in recommendations
        ]
        if not dry_run:
            try:
                repo.write_decisions(decision_rows)
            except Exception as exc:
                logger.exception("Failed to write decisions to Bitable: %s", exc)

        # ---- 9. Feishu push ----
        logger.info("Pushing Feishu cards…")
        summary_card = build_weekly_alert_card(
            urgent_count=report.urgent_count,
            planned_count=report.planned_count,
            dead_stock_count=report.dead_count,
            overstock_count=report.overstock_count,
            total_urgent_value_rmb=report.total_urgent_value_rmb,
            week_of=today,
        )
        if not dry_run:
            result = bot.send_card(summary_card)
            report.feishu_sent = bool(result.get("ok"))

            # Push top N urgent as individual cards
            for rec in urgent_list[:max_urgent_detail_cards]:
                detail_card = build_urgent_sku_card(
                    sku=rec.sku,
                    product_name=rec.product_name,
                    status=rec.status,
                    days_of_cover=rec.days_of_cover_remaining,
                    total_available=rec.total_available,
                    recommended_qty=rec.recommended_order_qty,
                    recommended_date=rec.recommended_order_date,
                    expected_arrival=rec.expected_arrival_date,
                    estimated_value_rmb=(
                        float(rec.estimated_order_value_rmb)
                        if rec.estimated_order_value_rmb is not None
                        else None
                    ),
                    notes=rec.notes,
                )
                bot.send_card(detail_card)

        return report

    except Exception as exc:
        logger.exception("Weekly alert workflow failed: %s", exc)
        report.error = str(exc)
        return report
