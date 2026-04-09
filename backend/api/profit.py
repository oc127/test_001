from decimal import Decimal
from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, literal_column, case
from sqlalchemy.orm import aliased
from database import get_db
from models import User, Store, StoreSku, Product, DailySale, ExchangeRate, VatConfig
from schemas import SkuProfitRow, ProfitSummary
from auth.jwt import get_current_user

router = APIRouter(prefix="/profit", tags=["profit"])


# ---------------------------------------------------------------------------
# Internal: build the base profit query (daily granularity)
# ---------------------------------------------------------------------------

async def _build_profit_rows(
    db: AsyncSession,
    tenant_id: int,
    date_from: date_type,
    date_to: date_type,
    store_id: int | None = None,
    category: str | None = None,
) -> list[dict]:
    """
    Return a list of dicts with all profit fields at daily + store + sku level.

    Join chain:
        daily_sales
        -> stores            (store_id, tenant filter)
        -> store_skus        (store_id + amazon_sku => product_id)
        -> products          (cogs, freight, duty)
        -> exchange_rates    (date + currency => rate_to_rmb)
        -> vat_config        (marketplace => standard_rate)

    For FX we use a correlated sub-query that finds the nearest available date
    when an exact match is missing.
    """

    # --- sub-query for nearest exchange rate ---
    fx_sub = (
        select(ExchangeRate.rate_to_rmb)
        .where(
            and_(
                ExchangeRate.currency == DailySale.currency,
                ExchangeRate.date <= DailySale.date,
            )
        )
        .order_by(ExchangeRate.date.desc())
        .limit(1)
        .correlate(DailySale)
        .scalar_subquery()
        .label("rate_to_rmb")
    )

    # --- main query ---
    query = (
        select(
            Store.store_name,
            Store.marketplace,
            DailySale.amazon_sku,
            Product.sku.label("internal_sku"),
            Product.title,
            Product.category,
            DailySale.date,
            DailySale.units_sold,
            DailySale.gross_revenue,
            DailySale.currency,
            DailySale.amazon_fees,
            DailySale.fba_fees,
            DailySale.storage_fees,
            DailySale.ad_spend,
            DailySale.refund_amount,
            DailySale.other_fees,
            Product.cogs_rmb,
            Product.freight_rmb_per_unit,
            Product.duty_rate_pct,
            fx_sub,
            VatConfig.standard_rate.label("vat_rate"),
        )
        .select_from(DailySale)
        .join(Store, DailySale.store_id == Store.id)
        .outerjoin(
            StoreSku,
            and_(
                StoreSku.store_id == DailySale.store_id,
                StoreSku.amazon_sku == DailySale.amazon_sku,
            ),
        )
        .outerjoin(Product, StoreSku.product_id == Product.id)
        .outerjoin(VatConfig, Store.marketplace == VatConfig.marketplace)
        .where(
            and_(
                Store.tenant_id == tenant_id,
                DailySale.date >= date_from,
                DailySale.date <= date_to,
            )
        )
    )

    if store_id is not None:
        query = query.where(DailySale.store_id == store_id)
    if category is not None:
        query = query.where(Product.category == category)

    query = query.order_by(DailySale.date, Store.store_name, DailySale.amazon_sku)

    result = await db.execute(query)
    rows = result.all()

    output: list[dict] = []
    for r in rows:
        rate = Decimal(str(r.rate_to_rmb)) if r.rate_to_rmb else Decimal("1")
        units = r.units_sold or 0
        gross = Decimal(str(r.gross_revenue or 0))
        amazon_fees = Decimal(str(r.amazon_fees or 0))
        fba_fees = Decimal(str(r.fba_fees or 0))
        storage_fees = Decimal(str(r.storage_fees or 0))
        ad_spend = Decimal(str(r.ad_spend or 0))
        refund_amount = Decimal(str(r.refund_amount or 0))
        other_fees = Decimal(str(r.other_fees or 0))
        total_amazon_costs = amazon_fees + fba_fees + storage_fees + ad_spend + refund_amount + other_fees

        cogs_unit = Decimal(str(r.cogs_rmb or 0))
        freight_unit = Decimal(str(r.freight_rmb_per_unit or 0))
        duty_pct = Decimal(str(r.duty_rate_pct or 0)) / Decimal("100")

        # Convert net revenue to RMB
        net_revenue_local = gross - total_amazon_costs
        net_revenue_rmb = net_revenue_local * rate

        # Cost calculations (all in RMB)
        cogs_rmb = cogs_unit * units
        freight_rmb = freight_unit * units
        duty_rmb = (cogs_rmb + freight_rmb) * duty_pct

        # VAT: gross_revenue * rate_to_rmb * standard_rate / (1 + standard_rate)
        vat_rate = Decimal(str(r.vat_rate or 0))
        if vat_rate > 0:
            vat_rmb = gross * rate * vat_rate / (1 + vat_rate)
        else:
            vat_rmb = Decimal("0")

        net_profit_rmb = net_revenue_rmb - cogs_rmb - freight_rmb - duty_rmb - vat_rmb

        if net_revenue_rmb != 0:
            margin_pct = (net_profit_rmb / net_revenue_rmb * 100).quantize(Decimal("0.01"))
        else:
            margin_pct = None

        output.append(
            {
                "store_name": r.store_name,
                "marketplace": r.marketplace,
                "amazon_sku": r.amazon_sku,
                "internal_sku": r.internal_sku,
                "title": r.title,
                "category": r.category,
                "date": r.date,
                "units_sold": units,
                "gross_revenue": gross.quantize(Decimal("0.01")),
                "currency": r.currency,
                "total_amazon_costs": total_amazon_costs.quantize(Decimal("0.01")),
                "net_revenue_rmb": net_revenue_rmb.quantize(Decimal("0.01")),
                "cogs_rmb": cogs_rmb.quantize(Decimal("0.01")),
                "freight_rmb": freight_rmb.quantize(Decimal("0.01")),
                "duty_rmb": duty_rmb.quantize(Decimal("0.01")),
                "vat_rmb": vat_rmb.quantize(Decimal("0.01")),
                "net_profit_rmb": net_profit_rmb.quantize(Decimal("0.01")),
                "margin_pct": margin_pct,
            }
        )

    return output


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _aggregate_by_sku(rows: list[dict]) -> list[dict]:
    """Group daily rows by store + amazon_sku, summing values."""
    buckets: dict[tuple, dict] = {}
    for r in rows:
        key = (r["store_name"], r["marketplace"], r["amazon_sku"])
        if key not in buckets:
            buckets[key] = {
                **r,
                "date": r["date"],  # keep first date seen
            }
        else:
            b = buckets[key]
            b["units_sold"] += r["units_sold"]
            b["gross_revenue"] += r["gross_revenue"]
            b["total_amazon_costs"] += r["total_amazon_costs"]
            b["net_revenue_rmb"] += r["net_revenue_rmb"]
            b["cogs_rmb"] += r["cogs_rmb"]
            b["freight_rmb"] += r["freight_rmb"]
            b["duty_rmb"] += r["duty_rmb"]
            b["vat_rmb"] += r["vat_rmb"]
            b["net_profit_rmb"] += r["net_profit_rmb"]

    result = []
    for b in buckets.values():
        if b["net_revenue_rmb"] != 0:
            b["margin_pct"] = (b["net_profit_rmb"] / b["net_revenue_rmb"] * 100).quantize(Decimal("0.01"))
        else:
            b["margin_pct"] = None
        result.append(b)
    return result


def _aggregate_by_store(rows: list[dict]) -> list[dict]:
    """Group daily rows by store, summing values."""
    buckets: dict[str, dict] = {}
    for r in rows:
        key = r["store_name"]
        if key not in buckets:
            buckets[key] = {
                **r,
                "amazon_sku": "*",
                "internal_sku": None,
                "title": None,
                "category": None,
            }
        else:
            b = buckets[key]
            b["units_sold"] += r["units_sold"]
            b["gross_revenue"] += r["gross_revenue"]
            b["total_amazon_costs"] += r["total_amazon_costs"]
            b["net_revenue_rmb"] += r["net_revenue_rmb"]
            b["cogs_rmb"] += r["cogs_rmb"]
            b["freight_rmb"] += r["freight_rmb"]
            b["duty_rmb"] += r["duty_rmb"]
            b["vat_rmb"] += r["vat_rmb"]
            b["net_profit_rmb"] += r["net_profit_rmb"]

    result = []
    for b in buckets.values():
        if b["net_revenue_rmb"] != 0:
            b["margin_pct"] = (b["net_profit_rmb"] / b["net_revenue_rmb"] * 100).quantize(Decimal("0.01"))
        else:
            b["margin_pct"] = None
        result.append(b)
    return result


# ---------------------------------------------------------------------------
# GET /profit/by-sku
# ---------------------------------------------------------------------------

@router.get("/by-sku", response_model=list[SkuProfitRow])
async def profit_by_sku(
    date_from: date_type = Query(...),
    date_to: date_type = Query(...),
    store_id: int | None = Query(None),
    category: str | None = Query(None),
    group_by: str = Query("daily", regex="^(daily|sku|store)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = await _build_profit_rows(
        db, current_user.tenant_id, date_from, date_to, store_id, category
    )

    if group_by == "sku":
        rows = _aggregate_by_sku(rows)
    elif group_by == "store":
        rows = _aggregate_by_store(rows)

    return rows


# ---------------------------------------------------------------------------
# GET /profit/summary
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=ProfitSummary)
async def profit_summary(
    date_from: date_type = Query(...),
    date_to: date_type = Query(...),
    store_id: int | None = Query(None),
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = await _build_profit_rows(
        db, current_user.tenant_id, date_from, date_to, store_id, category
    )

    if not rows:
        return ProfitSummary(
            total_revenue_rmb=Decimal("0"),
            total_profit_rmb=Decimal("0"),
            overall_margin_pct=Decimal("0"),
            total_units=0,
            profitable_skus=0,
            unprofitable_skus=0,
            by_store=[],
        )

    total_revenue = sum(r["net_revenue_rmb"] for r in rows)
    total_profit = sum(r["net_profit_rmb"] for r in rows)
    total_units = sum(r["units_sold"] for r in rows)

    if total_revenue != 0:
        overall_margin = (total_profit / total_revenue * 100).quantize(Decimal("0.01"))
    else:
        overall_margin = Decimal("0")

    # Profitable / unprofitable by SKU
    sku_profit: dict[str, Decimal] = {}
    for r in rows:
        key = f"{r['store_name']}|{r['amazon_sku']}"
        sku_profit[key] = sku_profit.get(key, Decimal("0")) + r["net_profit_rmb"]

    profitable = sum(1 for v in sku_profit.values() if v >= 0)
    unprofitable = sum(1 for v in sku_profit.values() if v < 0)

    # By-store breakdown
    store_agg: dict[str, dict] = {}
    for r in rows:
        sn = r["store_name"]
        if sn not in store_agg:
            store_agg[sn] = {
                "store_name": sn,
                "marketplace": r["marketplace"],
                "revenue_rmb": Decimal("0"),
                "profit_rmb": Decimal("0"),
                "units": 0,
            }
        store_agg[sn]["revenue_rmb"] += r["net_revenue_rmb"]
        store_agg[sn]["profit_rmb"] += r["net_profit_rmb"]
        store_agg[sn]["units"] += r["units_sold"]

    by_store = []
    for s in store_agg.values():
        if s["revenue_rmb"] != 0:
            s["margin_pct"] = (s["profit_rmb"] / s["revenue_rmb"] * 100).quantize(Decimal("0.01"))
        else:
            s["margin_pct"] = Decimal("0")
        # Quantize decimals for JSON
        s["revenue_rmb"] = s["revenue_rmb"].quantize(Decimal("0.01"))
        s["profit_rmb"] = s["profit_rmb"].quantize(Decimal("0.01"))
        by_store.append(s)

    return ProfitSummary(
        total_revenue_rmb=total_revenue.quantize(Decimal("0.01")),
        total_profit_rmb=total_profit.quantize(Decimal("0.01")),
        overall_margin_pct=overall_margin,
        total_units=total_units,
        profitable_skus=profitable,
        unprofitable_skus=unprofitable,
        by_store=by_store,
    )


# ---------------------------------------------------------------------------
# GET /profit/unprofitable
# ---------------------------------------------------------------------------

@router.get("/unprofitable", response_model=list[SkuProfitRow])
async def unprofitable_skus(
    date_from: date_type = Query(...),
    date_to: date_type = Query(...),
    store_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = await _build_profit_rows(
        db, current_user.tenant_id, date_from, date_to, store_id
    )

    # Aggregate by SKU first, then filter unprofitable
    aggregated = _aggregate_by_sku(rows)
    unprofitable = [r for r in aggregated if r["net_profit_rmb"] < 0]
    unprofitable.sort(key=lambda r: r["net_profit_rmb"])  # worst losses first
    return unprofitable
