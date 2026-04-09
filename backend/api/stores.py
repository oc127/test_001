from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from database import get_db
from models import User, Store
from schemas import StoreCreate, StoreUpdate, StoreOut
from auth.jwt import get_current_user
from config import get_settings

router = APIRouter(prefix="/stores", tags=["stores"])


def _store_to_out(store: Store) -> StoreOut:
    return StoreOut(
        id=store.id,
        store_code=store.store_code,
        store_name=store.store_name,
        marketplace=store.marketplace,
        currency=store.currency,
        seller_id=store.seller_id,
        is_active=store.is_active,
        has_api_credentials=bool(store.sp_api_refresh_token),
        created_at=store.created_at,
    )


@router.get("", response_model=list[StoreOut])
async def list_stores(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Store).where(Store.tenant_id == current_user.tenant_id)
    )
    stores = result.scalars().all()
    return [_store_to_out(s) for s in stores]


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(
    body: StoreCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    settings = get_settings()
    mp_info = settings.MARKETPLACES.get(body.marketplace)
    if not mp_info:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown marketplace '{body.marketplace}'. "
                   f"Valid: {list(settings.MARKETPLACES.keys())}",
        )

    # Check for duplicate store_code within tenant
    existing = await db.execute(
        select(Store).where(
            and_(
                Store.tenant_id == current_user.tenant_id,
                Store.store_code == body.store_code,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Store code already exists")

    store = Store(
        tenant_id=current_user.tenant_id,
        store_code=body.store_code,
        store_name=body.store_name,
        marketplace=body.marketplace,
        marketplace_id=mp_info["id"],
        currency=mp_info["currency"],
        seller_id=body.seller_id,
        sp_api_refresh_token=body.sp_api_refresh_token,
        sp_api_client_id=body.sp_api_client_id,
        sp_api_client_secret=body.sp_api_client_secret,
    )
    db.add(store)
    await db.commit()
    await db.refresh(store)
    return _store_to_out(store)


@router.get("/{store_id}", response_model=StoreOut)
async def get_store(
    store_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    store = await _get_tenant_store(db, store_id, current_user.tenant_id)
    return _store_to_out(store)


@router.patch("/{store_id}", response_model=StoreOut)
async def update_store(
    store_id: int,
    body: StoreUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    store = await _get_tenant_store(db, store_id, current_user.tenant_id)
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(store, field, value)
    await db.commit()
    await db.refresh(store)
    return _store_to_out(store)


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(
    store_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    store = await _get_tenant_store(db, store_id, current_user.tenant_id)
    await db.delete(store)
    await db.commit()


async def _get_tenant_store(
    db: AsyncSession, store_id: int, tenant_id: int
) -> Store:
    result = await db.execute(
        select(Store).where(
            and_(Store.id == store_id, Store.tenant_id == tenant_id)
        )
    )
    store = result.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    return store
