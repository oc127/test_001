"""Synthetic sample data for demos and smoke tests.

Two product lines, 8 SKUs total, ~90 days of sales history, a handful of
past POs, current inventory snapshots, and a previous snapshot from last week.

Designed so the reorder engine produces a mix of URGENT / PLANNED / SAFE /
OVERSTOCK / DEAD_STOCK results.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from ..models import (
    InventorySnapshot,
    Product,
    PurchaseOrder,
    SalesRecord,
)


def _make_history(
    sku: str,
    base: float,
    days: int,
    start: date,
    weekday_boost: float = 1.15,
    noise: float = 0.2,
    decline_slope: float = 0.0,
    rng: random.Random | None = None,
) -> list[SalesRecord]:
    rng = rng or random.Random(hash(sku) & 0xFFFFFFFF)
    records: list[SalesRecord] = []
    for i in range(days):
        d = start + timedelta(days=i)
        daily = base + decline_slope * i
        if d.weekday() >= 5:
            daily *= weekday_boost
        daily *= 1 + (rng.random() - 0.5) * 2 * noise
        units = max(0, int(round(daily)))
        records.append(SalesRecord(sku=sku, date=d, units_sold=units))
    return records


def build_sample_dataset(today: date | None = None) -> dict:
    """Build a complete synthetic dataset.

    Returns a dict with keys:
        products, sales_history, inventory_latest, inventory_previous, pos
    """
    today = today or date(2026, 4, 11)
    rng = random.Random(42)
    history_start = today - timedelta(days=90)

    products: list[Product] = [
        Product(
            sku="GX-001",
            name="古风木质摆件 A",
            product_line="工艺品",
            production_lead_days=30,
            shipping_lead_days=30,
            safety_stock_days=14,
            default_supplier="广东东莞 XX 工艺厂",
            moq=300,
            unit_cost_rmb=Decimal("28.50"),
        ),
        Product(
            sku="GX-002",
            name="手作陶瓷茶杯",
            product_line="工艺品",
            production_lead_days=30,
            shipping_lead_days=30,
            safety_stock_days=21,
            default_supplier="景德镇 YY 陶瓷",
            moq=500,
            unit_cost_rmb=Decimal("15.80"),
        ),
        Product(
            sku="GX-003",
            name="实木果盘套装",
            product_line="工艺品",
            production_lead_days=30,
            shipping_lead_days=30,
            safety_stock_days=14,
            default_supplier="广东东莞 XX 工艺厂",
            moq=300,
            unit_cost_rmb=Decimal("42.00"),
        ),
        Product(
            sku="GX-004",
            name="古风书签礼盒（停产候选）",
            product_line="工艺品",
            production_lead_days=30,
            shipping_lead_days=30,
            safety_stock_days=14,
            default_supplier="义乌 WW 文创",
            moq=500,
            unit_cost_rmb=Decimal("8.20"),
        ),
        Product(
            sku="3C-100",
            name="无线充电器 10W",
            product_line="3C",
            production_lead_days=5,
            shipping_lead_days=15,
            safety_stock_days=10,
            default_supplier="深圳 ZZ 电子",
            moq=500,
            unit_cost_rmb=Decimal("22.00"),
        ),
        Product(
            sku="3C-101",
            name="蓝牙耳机 TWS",
            product_line="3C",
            production_lead_days=5,
            shipping_lead_days=15,
            safety_stock_days=14,
            default_supplier="深圳 QQ 科技",
            moq=1000,
            unit_cost_rmb=Decimal("68.00"),
        ),
        Product(
            sku="3C-102",
            name="USB-C 快充线 2m",
            product_line="3C",
            production_lead_days=5,
            shipping_lead_days=15,
            safety_stock_days=10,
            default_supplier="深圳 ZZ 电子",
            moq=2000,
            unit_cost_rmb=Decimal("4.50"),
        ),
        Product(
            sku="3C-103",
            name="车载手机支架",
            product_line="3C",
            production_lead_days=5,
            shipping_lead_days=15,
            safety_stock_days=10,
            default_supplier="东莞 RR 配件",
            moq=1000,
            unit_cost_rmb=Decimal("6.80"),
        ),
    ]

    # Build sales history with varying profiles
    sales_history: list[SalesRecord] = []
    sales_history += _make_history("GX-001", base=15, days=90, start=history_start, rng=rng)
    sales_history += _make_history("GX-002", base=8, days=90, start=history_start, rng=rng)
    sales_history += _make_history("GX-003", base=20, days=90, start=history_start, rng=rng)
    # GX-004 = dying product — strong decline
    sales_history += _make_history(
        "GX-004", base=10, days=90, start=history_start, decline_slope=-0.09, rng=rng
    )
    sales_history += _make_history("3C-100", base=50, days=90, start=history_start, rng=rng)
    sales_history += _make_history("3C-101", base=12, days=90, start=history_start, rng=rng)
    sales_history += _make_history("3C-102", base=80, days=90, start=history_start, rng=rng)
    # 3C-103 = accelerating
    sales_history += _make_history(
        "3C-103", base=5, days=90, start=history_start, decline_slope=0.08, rng=rng
    )

    # Current inventory — designed to trigger different statuses
    #   GX-001 URGENT: low stock, short cover
    #   GX-002 PLANNED: moderate cover
    #   GX-003 SAFE: comfortable
    #   GX-004 OVERSTOCK + DEAD: piled up + declining
    #   3C-100 URGENT
    #   3C-101 SAFE
    #   3C-102 URGENT
    #   3C-103 PLANNED
    inventory_latest: dict[str, InventorySnapshot] = {
        "GX-001": InventorySnapshot("GX-001", today, 150, 0, 0),
        "GX-002": InventorySnapshot("GX-002", today, 200, 500, 0),
        "GX-003": InventorySnapshot("GX-003", today, 2500, 0, 500),
        "GX-004": InventorySnapshot("GX-004", today, 3000, 0, 0),  # overstock + dying
        "3C-100": InventorySnapshot("3C-100", today, 400, 0, 0),
        "3C-101": InventorySnapshot("3C-101", today, 1500, 0, 1000),
        "3C-102": InventorySnapshot("3C-102", today, 600, 0, 0),
        "3C-103": InventorySnapshot("3C-103", today, 350, 500, 0),
    }
    # Previous snapshot (1 week ago) for swing detection
    last_week = today - timedelta(days=7)
    inventory_previous: dict[str, InventorySnapshot] = {
        "GX-001": InventorySnapshot("GX-001", last_week, 260, 0, 0),
        "GX-002": InventorySnapshot("GX-002", last_week, 260, 500, 0),
        "GX-003": InventorySnapshot("GX-003", last_week, 2640, 0, 500),
        "GX-004": InventorySnapshot("GX-004", last_week, 3060, 0, 0),
        "3C-100": InventorySnapshot("3C-100", last_week, 750, 0, 0),
        "3C-101": InventorySnapshot("3C-101", last_week, 1580, 0, 1000),
        "3C-102": InventorySnapshot("3C-102", last_week, 1200, 0, 0),
        "3C-103": InventorySnapshot("3C-103", last_week, 380, 500, 0),
    }

    # Historical POs — some arrived on time, some late (to feed supplier learning)
    pos: list[PurchaseOrder] = [
        PurchaseOrder(
            po_number="PO-20260115-GZ001-001",
            sku="GX-001",
            order_date=date(2026, 1, 15),
            qty=500,
            unit_price_rmb=Decimal("28.50"),
            supplier="广东东莞 XX 工艺厂",
            status="received",
            expected_arrival_date=date(2026, 3, 16),
            actual_arrival_date=date(2026, 3, 20),  # 4 days late
        ),
        PurchaseOrder(
            po_number="PO-20260201-GZ001-001",
            sku="GX-003",
            order_date=date(2026, 2, 1),
            qty=500,
            unit_price_rmb=Decimal("42.00"),
            supplier="广东东莞 XX 工艺厂",
            status="received",
            expected_arrival_date=date(2026, 4, 2),
            actual_arrival_date=date(2026, 4, 1),  # on time
        ),
        PurchaseOrder(
            po_number="PO-20260108-JZYY-001",
            sku="GX-002",
            order_date=date(2026, 1, 8),
            qty=1000,
            unit_price_rmb=Decimal("15.80"),
            supplier="景德镇 YY 陶瓷",
            status="received",
            expected_arrival_date=date(2026, 3, 9),
            actual_arrival_date=date(2026, 3, 25),  # 16 days late ⚠️
        ),
        PurchaseOrder(
            po_number="PO-20260215-JZYY-001",
            sku="GX-002",
            order_date=date(2026, 2, 15),
            qty=500,
            unit_price_rmb=Decimal("15.80"),
            supplier="景德镇 YY 陶瓷",
            status="in_transit",
            expected_arrival_date=date(2026, 4, 16),
        ),
        PurchaseOrder(
            po_number="PO-20260310-SZZZ-001",
            sku="3C-100",
            order_date=date(2026, 3, 10),
            qty=500,
            unit_price_rmb=Decimal("22.00"),
            supplier="深圳 ZZ 电子",
            status="received",
            expected_arrival_date=date(2026, 3, 30),
            actual_arrival_date=date(2026, 3, 31),
        ),
        PurchaseOrder(
            po_number="PO-20260320-SZQQ-001",
            sku="3C-101",
            order_date=date(2026, 3, 20),
            qty=1000,
            unit_price_rmb=Decimal("68.00"),
            supplier="深圳 QQ 科技",
            status="in_production",
            expected_arrival_date=date(2026, 4, 9),
        ),
        PurchaseOrder(
            po_number="PO-20260325-DGRR-001",
            sku="3C-103",
            order_date=date(2026, 3, 25),
            qty=500,
            unit_price_rmb=Decimal("6.80"),
            supplier="东莞 RR 配件",
            status="in_transit",
            expected_arrival_date=date(2026, 4, 14),
        ),
    ]

    return {
        "today": today,
        "products": products,
        "sales_history": sales_history,
        "inventory_latest": inventory_latest,
        "inventory_previous": inventory_previous,
        "pos": pos,
    }
