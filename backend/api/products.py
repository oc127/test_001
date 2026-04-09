import io
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from database import get_db
from models import User, Product, StoreSku, Store
from schemas import (
    ProductCreate,
    ProductUpdate,
    ProductOut,
    CsvImportResult,
    StoreSkuCreate,
    StoreSkuOut,
)
from auth.jwt import get_current_user
import pandas as pd

router = APIRouter(prefix="/products", tags=["products"])


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Product).where(Product.tenant_id == current_user.tenant_id)
    )
    return result.scalars().all()


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = await db.execute(
        select(Product).where(
            and_(
                Product.tenant_id == current_user.tenant_id,
                Product.sku == body.sku,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Product with this SKU already exists")

    product = Product(tenant_id=current_user.tenant_id, **body.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _get_tenant_product(db, product_id, current_user.tenant_id)


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = await _get_tenant_product(db, product_id, current_user.tenant_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = await _get_tenant_product(db, product_id, current_user.tenant_id)
    await db.delete(product)
    await db.commit()


# ---------------------------------------------------------------------------
# CSV Import  (upsert by tenant_id + sku)
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "sku", "title", "supplier", "cogs_rmb", "freight_rmb_per_unit",
    "duty_rate_pct", "notes",
]
REQUIRED_COLUMNS = ["sku"]


@router.post("/import-csv", response_model=CsvImportResult)
async def import_products_csv(
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

    # Normalise column names
    df.columns = [c.strip().lower() for c in df.columns]
    for req in REQUIRED_COLUMNS:
        if req not in df.columns:
            raise HTTPException(status_code=400, detail=f"Missing required column: {req}")

    imported = 0
    updated = 0
    errors: list[str] = []

    for idx, row in df.iterrows():
        row_num = idx + 2  # 1-indexed header + data
        sku = str(row.get("sku", "")).strip()
        if not sku:
            errors.append(f"Row {row_num}: empty SKU, skipped")
            continue

        # Build field dict from CSV row
        fields: dict = {}
        for col in CSV_COLUMNS:
            if col == "sku":
                continue
            val = row.get(col)
            if pd.isna(val):
                continue
            if col in ("cogs_rmb", "freight_rmb_per_unit", "duty_rate_pct"):
                try:
                    fields[col] = Decimal(str(val))
                except (InvalidOperation, ValueError):
                    errors.append(f"Row {row_num}: invalid number for {col}")
                    continue
            else:
                fields[col] = str(val).strip()

        # Upsert: look for existing product by tenant + sku
        result = await db.execute(
            select(Product).where(
                and_(
                    Product.tenant_id == current_user.tenant_id,
                    Product.sku == sku,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            for field, value in fields.items():
                setattr(existing, field, value)
            updated += 1
        else:
            product = Product(tenant_id=current_user.tenant_id, sku=sku, **fields)
            db.add(product)
            imported += 1

    await db.commit()
    return CsvImportResult(imported=imported, updated=updated, errors=errors)


# ---------------------------------------------------------------------------
# Store-SKU mappings
# ---------------------------------------------------------------------------

@router.post("/{product_id}/store-skus", response_model=StoreSkuOut, status_code=status.HTTP_201_CREATED)
async def create_store_sku(
    product_id: int,
    body: StoreSkuCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate product belongs to tenant
    await _get_tenant_product(db, product_id, current_user.tenant_id)

    # Validate store belongs to tenant
    store_result = await db.execute(
        select(Store).where(
            and_(Store.id == body.store_id, Store.tenant_id == current_user.tenant_id)
        )
    )
    if not store_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Store not found")

    # Check for duplicate
    dup = await db.execute(
        select(StoreSku).where(
            and_(StoreSku.store_id == body.store_id, StoreSku.amazon_sku == body.amazon_sku)
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Store SKU mapping already exists")

    store_sku = StoreSku(
        store_id=body.store_id,
        product_id=product_id,
        amazon_sku=body.amazon_sku,
        asin=body.asin,
        fnsku=body.fnsku,
    )
    db.add(store_sku)
    await db.commit()
    await db.refresh(store_sku)
    return store_sku


@router.get("/{product_id}/store-skus", response_model=list[StoreSkuOut])
async def list_store_skus(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_tenant_product(db, product_id, current_user.tenant_id)
    result = await db.execute(
        select(StoreSku).where(StoreSku.product_id == product_id)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_tenant_product(
    db: AsyncSession, product_id: int, tenant_id: int
) -> Product:
    result = await db.execute(
        select(Product).where(
            and_(Product.id == product_id, Product.tenant_id == tenant_id)
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product
