"""Draft Purchase Order generator.

Why this exists
---------------
The weekly alert gives the user "here's what to order". The buyer doesn't
want to hand-type the PO — they want a draft row pre-filled in the PO
table, ready to review → approve → send to factory.

This module:
1. Takes a list of approved ReorderRecommendations.
2. Generates human-readable PO numbers (date + supplier code + counter).
3. Creates PurchaseOrder drafts with status='draft'.
4. Writes them to the Bitable PO table.
5. Exports a supplier-facing CSV (per supplier) the buyer can paste into
   WeChat with the factory.

Closure loop
------------
Once created with status='draft', these POs already contribute to
`pending_po_qty` via `is_active=False`? No — only 'ordered' and later
statuses count. So drafts don't inflate coverage until the buyer moves
them to 'ordered'. This keeps the numbers honest.
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Optional

from ..config import DATA_OUT_DIR
from ..models import Product, PurchaseOrder, ReorderRecommendation
from ..clients.bitable import InventoryBotRepository

logger = logging.getLogger(__name__)


# ===== PO number generator =====

_po_counter: dict[str, int] = defaultdict(int)


def generate_po_number(
    order_date: date,
    supplier: Optional[str],
    existing_numbers: Optional[set[str]] = None,
) -> str:
    """Build a PO number like `PO-20260413-SUP1-001`.

    `existing_numbers` is mutated in place so callers can reuse the same
    set to guarantee uniqueness across a batch.
    """
    if existing_numbers is None:
        existing_numbers = set()
    sup_code = "".join(
        c for c in (supplier or "UNK") if c.isascii() and c.isalnum()
    )[:6].upper() or "UNK"
    prefix = f"PO-{order_date.strftime('%Y%m%d')}-{sup_code}-"
    # Find next available counter
    n = 1
    while True:
        candidate = f"{prefix}{n:03d}"
        if candidate not in existing_numbers:
            existing_numbers.add(candidate)
            return candidate
        n += 1


# ===== Generation =====

@dataclass
class POGenerationResult:
    drafts: list[PurchaseOrder] = field(default_factory=list)
    per_supplier_csv_paths: dict[str, Path] = field(default_factory=dict)
    total_value_rmb: Decimal = Decimal("0")
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (sku, reason)


def generate_draft_pos(
    recommendations: list[ReorderRecommendation],
    products_by_sku: dict[str, Product],
    existing_po_numbers: Optional[set[str]] = None,
    run_date: Optional[date] = None,
) -> POGenerationResult:
    """Turn URGENT/PLANNED recommendations into draft POs.

    SAFE, DEAD_STOCK, OVERSTOCK are skipped.
    """
    run_date = run_date or date.today()
    existing_po_numbers = set(existing_po_numbers or [])
    result = POGenerationResult()

    for rec in recommendations:
        if rec.recommended_order_qty <= 0:
            result.skipped.append((rec.sku, "数量为 0"))
            continue
        if rec.status not in ("🔴急单", "🟡计划"):
            result.skipped.append((rec.sku, f"状态 {rec.status}"))
            continue

        product = products_by_sku.get(rec.sku)
        if product is None:
            result.skipped.append((rec.sku, "找不到产品主数据"))
            continue

        supplier = product.default_supplier
        po_number = generate_po_number(
            order_date=rec.recommended_order_date,
            supplier=supplier,
            existing_numbers=existing_po_numbers,
        )

        draft = PurchaseOrder(
            po_number=po_number,
            sku=rec.sku,
            order_date=rec.recommended_order_date,
            qty=rec.recommended_order_qty,
            unit_price_rmb=product.unit_cost_rmb,
            supplier=supplier,
            status="draft",
            expected_production_done=(
                rec.expected_arrival_date  # rough; buyer will adjust
            ),
            expected_arrival_date=rec.expected_arrival_date,
            notes=(
                f"系统自动生成 {run_date.strftime('%Y-%m-%d')} | "
                f"剩余 {rec.days_of_cover_remaining:.0f} 天 | {rec.status}"
            ),
        )
        result.drafts.append(draft)
        if draft.total_rmb:
            result.total_value_rmb += draft.total_rmb

    return result


def export_supplier_csvs(
    drafts: list[PurchaseOrder],
    products_by_sku: dict[str, Product],
    output_dir: Path = DATA_OUT_DIR,
    run_date: Optional[date] = None,
) -> dict[str, Path]:
    """Write one CSV per supplier containing their orders — for WeChat/email."""
    run_date = run_date or date.today()
    output_dir.mkdir(parents=True, exist_ok=True)
    per_supplier: dict[str, list[PurchaseOrder]] = defaultdict(list)
    for d in drafts:
        per_supplier[d.supplier or "(未指定供应商)"].append(d)

    paths: dict[str, Path] = {}
    for supplier, orders in per_supplier.items():
        safe_name = "".join(c if c.isalnum() else "_" for c in supplier)
        csv_path = output_dir / f"PO_{run_date.strftime('%Y%m%d')}_{safe_name}.csv"
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "PO编号",
                "SKU",
                "产品名称",
                "数量",
                "MOQ",
                "预估单价RMB",
                "预估总金额RMB",
                "下单日期",
                "预计到货日期",
                "备注",
            ]
        )
        for o in orders:
            p = products_by_sku.get(o.sku)
            name = p.name if p else ""
            moq = p.moq if p else ""
            writer.writerow(
                [
                    o.po_number,
                    o.sku,
                    name,
                    o.qty,
                    moq,
                    float(o.unit_price_rmb) if o.unit_price_rmb is not None else "",
                    float(o.total_rmb) if o.total_rmb is not None else "",
                    o.order_date.strftime("%Y-%m-%d"),
                    (o.expected_arrival_date or "").strftime("%Y-%m-%d")
                    if o.expected_arrival_date
                    else "",
                    o.notes,
                ]
            )
        csv_path.write_text(buffer.getvalue(), encoding="utf-8-sig")  # BOM for Excel
        paths[supplier] = csv_path

    return paths


def run_po_generation(
    recommendations: list[ReorderRecommendation],
    products: list[Product],
    repo: Optional[InventoryBotRepository] = None,
    write_to_bitable: bool = True,
    export_csvs: bool = True,
    run_date: Optional[date] = None,
) -> POGenerationResult:
    """End-to-end: generate drafts, export CSVs, write to Bitable."""
    run_date = run_date or date.today()
    products_by_sku = {p.sku: p for p in products}
    repo = repo or InventoryBotRepository()

    # Reserve any existing PO numbers to avoid collisions
    existing_numbers: set[str] = set()
    try:
        existing_pos = repo.fetch_purchase_orders()
        existing_numbers = {p.po_number for p in existing_pos}
    except Exception as exc:
        logger.warning("Could not fetch existing POs for number collision check: %s", exc)

    result = generate_draft_pos(
        recommendations=recommendations,
        products_by_sku=products_by_sku,
        existing_po_numbers=existing_numbers,
        run_date=run_date,
    )

    if export_csvs and result.drafts:
        result.per_supplier_csv_paths = export_supplier_csvs(
            drafts=result.drafts,
            products_by_sku=products_by_sku,
            run_date=run_date,
        )

    if write_to_bitable and result.drafts:
        try:
            repo.create_draft_pos(result.drafts)
        except Exception as exc:
            logger.exception("Failed to write draft POs to Bitable: %s", exc)

    return result
