from pydantic import BaseModel, EmailStr
from decimal import Decimal
from datetime import date, datetime


# === Auth ===
class RegisterRequest(BaseModel):
    email: str
    password: str
    tenant_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: int
    user_id: int


# === Store ===
class StoreCreate(BaseModel):
    store_code: str
    store_name: str
    marketplace: str  # "US", "DE", "JP", etc.
    seller_id: str | None = None
    sp_api_refresh_token: str | None = None
    sp_api_client_id: str | None = None
    sp_api_client_secret: str | None = None


class StoreUpdate(BaseModel):
    store_name: str | None = None
    seller_id: str | None = None
    sp_api_refresh_token: str | None = None
    sp_api_client_id: str | None = None
    sp_api_client_secret: str | None = None
    is_active: bool | None = None


class StoreOut(BaseModel):
    id: int
    store_code: str
    store_name: str
    marketplace: str
    currency: str
    seller_id: str | None
    is_active: bool
    has_api_credentials: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# === Product ===
class ProductCreate(BaseModel):
    sku: str
    asin: str | None = None
    title: str | None = None
    category: str | None = None
    supplier: str | None = None
    cogs_rmb: Decimal | None = None
    freight_rmb_per_unit: Decimal | None = None
    duty_rate_pct: Decimal = Decimal("0")
    notes: str | None = None


class ProductUpdate(BaseModel):
    asin: str | None = None
    title: str | None = None
    category: str | None = None
    supplier: str | None = None
    cogs_rmb: Decimal | None = None
    freight_rmb_per_unit: Decimal | None = None
    duty_rate_pct: Decimal | None = None
    notes: str | None = None


class ProductOut(BaseModel):
    id: int
    sku: str
    asin: str | None
    title: str | None
    category: str | None
    supplier: str | None
    cogs_rmb: Decimal | None
    freight_rmb_per_unit: Decimal | None
    duty_rate_pct: Decimal
    notes: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class CsvImportResult(BaseModel):
    imported: int
    updated: int
    errors: list[str]


# === Store SKU Mapping ===
class StoreSkuCreate(BaseModel):
    store_id: int
    product_id: int
    amazon_sku: str
    asin: str | None = None
    fnsku: str | None = None


class StoreSkuOut(BaseModel):
    id: int
    store_id: int
    product_id: int
    amazon_sku: str
    asin: str | None
    fnsku: str | None
    is_active: bool

    model_config = {"from_attributes": True}


# === Daily Sales (CSV import) ===
class DailySaleRow(BaseModel):
    store_code: str
    amazon_sku: str
    date: date
    units_sold: int = 0
    gross_revenue: Decimal = Decimal("0")
    amazon_fees: Decimal = Decimal("0")
    fba_fees: Decimal = Decimal("0")
    storage_fees: Decimal = Decimal("0")
    ad_spend: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    other_fees: Decimal = Decimal("0")


# === Profit ===
class SkuProfitRow(BaseModel):
    store_name: str
    marketplace: str
    amazon_sku: str
    internal_sku: str | None
    title: str | None
    category: str | None
    date: date
    units_sold: int
    gross_revenue: Decimal
    currency: str
    total_amazon_costs: Decimal
    net_revenue_rmb: Decimal
    cogs_rmb: Decimal
    freight_rmb: Decimal
    duty_rmb: Decimal
    vat_rmb: Decimal
    net_profit_rmb: Decimal
    margin_pct: Decimal | None


class ProfitSummary(BaseModel):
    total_revenue_rmb: Decimal
    total_profit_rmb: Decimal
    overall_margin_pct: Decimal
    total_units: int
    profitable_skus: int
    unprofitable_skus: int
    by_store: list[dict]
