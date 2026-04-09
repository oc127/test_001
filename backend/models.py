from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    String, Integer, Numeric, Boolean, Date, DateTime, Text, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(50), default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    stores: Mapped[list["Store"]] = relationship(back_populates="tenant")
    products: Mapped[list["Product"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    store_code: Mapped[str] = mapped_column(String(100))
    store_name: Mapped[str] = mapped_column(String(200))
    marketplace_id: Mapped[str] = mapped_column(String(50))
    marketplace: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    seller_id: Mapped[str | None] = mapped_column(String(100))
    sp_api_refresh_token: Mapped[str | None] = mapped_column(Text)
    sp_api_client_id: Mapped[str | None] = mapped_column(String(200))
    sp_api_client_secret: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "store_code"),)

    tenant: Mapped["Tenant"] = relationship(back_populates="stores")
    store_skus: Mapped[list["StoreSku"]] = relationship(back_populates="store")
    daily_sales: Mapped[list["DailySale"]] = relationship(back_populates="store")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    sku: Mapped[str] = mapped_column(String(100))
    asin: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(200))
    supplier: Mapped[str | None] = mapped_column(String(300))
    cogs_rmb: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    freight_rmb_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    duty_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)

    tenant: Mapped["Tenant"] = relationship(back_populates="products")
    store_skus: Mapped[list["StoreSku"]] = relationship(back_populates="product")


class StoreSku(Base):
    __tablename__ = "store_skus"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    amazon_sku: Mapped[str] = mapped_column(String(100))
    asin: Mapped[str | None] = mapped_column(String(20))
    fnsku: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("store_id", "amazon_sku"),)

    store: Mapped["Store"] = relationship(back_populates="store_skus")
    product: Mapped["Product"] = relationship(back_populates="store_skus")


class DailySale(Base):
    __tablename__ = "daily_sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    amazon_sku: Mapped[str] = mapped_column(String(100))
    date: Mapped[date] = mapped_column(Date)
    units_sold: Mapped[int] = mapped_column(Integer, default=0)
    gross_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    amazon_fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    fba_fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    storage_fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    ad_spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    other_fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(10))

    __table_args__ = (UniqueConstraint("store_id", "amazon_sku", "date"),)

    store: Mapped["Store"] = relationship(back_populates="daily_sales")


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(10), primary_key=True)
    rate_to_rmb: Mapped[Decimal] = mapped_column(Numeric(12, 6))


class VatConfig(Base):
    __tablename__ = "vat_config"

    marketplace: Mapped[str] = mapped_column(String(10), primary_key=True)
    standard_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    notes: Mapped[str | None] = mapped_column(Text)
