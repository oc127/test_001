"""Domain models (dataclasses) — framework-agnostic."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional


@dataclass
class Product:
    sku: str
    name: str
    product_line: str  # "工艺品" / "3C"
    production_lead_days: int
    shipping_lead_days: int
    safety_stock_days: int = 14
    default_supplier: Optional[str] = None
    supplier_1688_url: Optional[str] = None
    moq: int = 300
    unit_cost_rmb: Optional[Decimal] = None
    notes: str = ""

    @property
    def total_lead_days(self) -> int:
        return self.production_lead_days + self.shipping_lead_days


@dataclass
class SalesRecord:
    sku: str
    date: date
    units_sold: int
    store: Optional[str] = None


@dataclass
class ForecastRecord:
    sku: str
    date: date
    forecast_units: int
    source: str = "baseline"  # 'baseline' / 'manual' / 'promotion'


@dataclass
class InventorySnapshot:
    sku: str
    snapshot_date: date
    qty_in_stock: int
    qty_in_transit: int
    qty_in_production: int
    store: Optional[str] = None

    @property
    def total_available(self) -> int:
        return self.qty_in_stock + self.qty_in_transit + self.qty_in_production


@dataclass
class PurchaseOrder:
    po_number: str
    sku: str
    order_date: date
    qty: int
    unit_price_rmb: Optional[Decimal]
    supplier: Optional[str]
    status: str  # draft / ordered / in_production / in_transit / arrived / received / cancelled
    expected_production_done: Optional[date] = None
    expected_arrival_date: Optional[date] = None
    actual_arrival_date: Optional[date] = None
    notes: str = ""

    @property
    def total_rmb(self) -> Optional[Decimal]:
        if self.unit_price_rmb is None:
            return None
        return Decimal(self.qty) * self.unit_price_rmb

    @property
    def is_active(self) -> bool:
        """Whether this PO contributes to future availability."""
        return self.status in ("ordered", "in_production", "in_transit", "arrived")


@dataclass
class ReorderRecommendation:
    sku: str
    product_name: str
    product_line: str
    status: str  # from ReorderStatus

    # Current state
    current_in_stock: int
    current_in_transit: int
    current_in_production: int
    pending_po_qty: int
    total_available: int

    # Demand
    avg_daily_sales: float
    std_daily_sales: float
    demand_in_lead_time: int
    demand_in_horizon: int  # 60-day horizon for ordering
    days_of_cover_remaining: float

    # Lead time (with holiday adjustment)
    base_lead_days: int
    adjusted_lead_days: int
    lead_time_adjustment_reason: str

    # Reorder decision
    reorder_point: int
    coverage_gap: int  # negative = shortfall
    recommended_order_qty: int
    recommended_order_date: date
    expected_arrival_date: date
    estimated_order_value_rmb: Optional[Decimal] = None

    # Urgency ranking
    urgency_score: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class DataQualityIssue:
    sku: str
    severity: str  # 'error' / 'warning' / 'info'
    field: str
    message: str
    suggested_action: str = ""


@dataclass
class DeadStockAlert:
    sku: str
    product_name: str
    weeks_declining: int
    decline_pct: float
    current_stock: int
    days_to_deplete: float
    suggested_action: str


@dataclass
class PriceAlert:
    sku: str
    supplier: str
    old_price_rmb: Decimal
    new_price_rmb: Decimal
    change_pct: float
    change_direction: str  # 'up' / 'down'
    url: str
    detected_at: datetime
    recommendation: str
