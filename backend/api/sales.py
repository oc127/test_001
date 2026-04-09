import io
from decimal import Decimal, InvalidOperation
from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from database import get_db
from models import User, Store, DailySale
from schemas import CsvImportResult, DailySaleRow
from auth.jwt import get_current_user
import pandas as pd

router = APIRouter(prefix="/sales", tags=["sales"])

SALES_CSV_COLUMNS = [
    "store_code", "amazon_sku", "date", "units_sold", "gross_revenue",
    "amazon_fees", "fba_fees", "storage_fees", "ad_spend", "refund_amount",
    "other_fees",
]
REQUIRED_SALES_COLUMNS = ["store_code", "amazon_sku", "date"]


@router.post("/import-csv", response_model=CsvImportResult)
async def import_sales_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    content = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot parse CSV: {exc}")

    df.columns = [c.strip().lower() for c in df.columns]
    for req in REQUIRED_SALES_COLUMNS:
        if req not in df.columns:
            raise HTTPException(status_code=400, detail=f"Missing required column: {req}")

    # Pre-load store lookup for this tenant: store_code -> Store
    stores_result = await db.execute(
        select(Store).where(Store.tenant_id == current_user.tenant_id)
    )
    store_map: dict[str, Store] = {
        s.store_code: s for s in stores_result.scalars().all()
    }

    imported = 0
    updated = 0
    errors: list[str] = []

    for idx, row in df.iterrows():
        row_num = idx + 2

        store_code = str(row.get("store_code", "")).strip()
        amazon_sku = str(row.get("amazon_sku", "")).strip()
        date_str = str(row.get("date", "")).strip()

        if not store_code or not amazon_sku or not date_str:
            errors.append(f"Row {row_num}: missing required field(s), skipped")
            continue

        store = store_map.get(store_code)
        if not store:
            errors.append(f"Row {row_num}: unknown store_code '{store_code}', skipped")
            continue

        # Parse date
        try:
            sale_date = pd.to_datetime(date_str).date()
        except Exception:
            errors.append(f"Row {row_num}: invalid date '{date_str}', skipped")
            continue

        # Parse numeric fields
        numeric_fields = {
            "units_sold": 0,
            "gross_revenue": Decimal("0"),
            "amazon_fees": Decimal("0"),
            "fba_fees": Decimal("0"),
            "storage_fees": Decimal("0"),
            "ad_spend": Decimal("0"),
            "refund_amount": Decimal("0"),
            "other_fees": Decimal("0"),
        }
        parsed: dict = {}
        row_ok = True
        for field, default in numeric_fields.items():
            val = row.get(field)
            if pd.isna(val) if not isinstance(val, str) else (val.strip() == ""):
                parsed[field] = default
                continue
            try:
                if field == "units_sold":
                    parsed[field] = int(float(val))
                else:
                    parsed[field] = Decimal(str(val))
            except (InvalidOperation, ValueError, TypeError):
                errors.append(f"Row {row_num}: invalid number for {field}, skipped")
                row_ok = False
                break
        if not row_ok:
            continue

        # Upsert: check for existing record by store_id + amazon_sku + date
        result = await db.execute(
            select(DailySale).where(
                and_(
                    DailySale.store_id == store.id,
                    DailySale.amazon_sku == amazon_sku,
                    DailySale.date == sale_date,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            for field, value in parsed.items():
                setattr(existing, field, value)
            updated += 1
        else:
            sale = DailySale(
                store_id=store.id,
                amazon_sku=amazon_sku,
                date=sale_date,
                currency=store.currency,
                **parsed,
            )
            db.add(sale)
            imported += 1

    await db.commit()
    return CsvImportResult(imported=imported, updated=updated, errors=errors)


@router.get("")
async def list_sales(
    store_id: int | None = Query(None),
    date_from: date_type | None = Query(None),
    date_to: date_type | None = Query(None),
    sku: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List daily sales with optional filters. Only returns sales for tenant's stores."""
    # Build subquery of tenant store IDs
    store_ids_q = select(Store.id).where(Store.tenant_id == current_user.tenant_id)

    query = select(DailySale).where(DailySale.store_id.in_(store_ids_q))

    if store_id is not None:
        # Verify store belongs to tenant
        store_check = await db.execute(
            select(Store).where(
                and_(Store.id == store_id, Store.tenant_id == current_user.tenant_id)
            )
        )
        if not store_check.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Store not found")
        query = query.where(DailySale.store_id == store_id)

    if date_from is not None:
        query = query.where(DailySale.date >= date_from)
    if date_to is not None:
        query = query.where(DailySale.date <= date_to)
    if sku is not None:
        query = query.where(DailySale.amazon_sku == sku)

    query = query.order_by(DailySale.date.desc(), DailySale.amazon_sku)
    result = await db.execute(query)
    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "store_id": r.store_id,
            "amazon_sku": r.amazon_sku,
            "date": r.date.isoformat(),
            "units_sold": r.units_sold,
            "gross_revenue": str(r.gross_revenue),
            "amazon_fees": str(r.amazon_fees),
            "fba_fees": str(r.fba_fees),
            "storage_fees": str(r.storage_fees),
            "ad_spend": str(r.ad_spend),
            "refund_amount": str(r.refund_amount),
            "other_fees": str(r.other_fees),
            "currency": r.currency,
        }
        for r in rows
    ]
