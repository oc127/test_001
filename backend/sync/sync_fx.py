from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import httpx
from database import get_db
from models import ExchangeRate
from config import get_settings
from auth.jwt import get_current_user
from models import User

router = APIRouter(prefix="/sync", tags=["sync"])

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]


async def sync_exchange_rates(db: AsyncSession, days_back: int = 90) -> dict:
    """
    Fetch exchange rates from frankfurter.app for each currency and upsert
    into the exchange_rates table.

    Uses the date-range endpoint to batch requests per currency:
        GET https://api.frankfurter.app/{start}..{end}?from={currency}&to=CNY

    Returns a summary dict with counts.
    """
    settings = get_settings()
    base_url = settings.FX_API_URL

    today = date.today()
    start_date = today - timedelta(days=days_back)

    # Find which dates already exist per currency
    result = await db.execute(
        select(ExchangeRate.currency, ExchangeRate.date).where(
            and_(
                ExchangeRate.date >= start_date,
                ExchangeRate.date <= today,
            )
        )
    )
    existing: set[tuple[str, date]] = {(row.currency, row.date) for row in result.all()}

    total_fetched = 0
    total_inserted = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for currency in CURRENCIES:
            # Check if we actually need any dates for this currency
            need_dates = False
            d = start_date
            while d <= today:
                if (currency, d) not in existing:
                    need_dates = True
                    break
                d += timedelta(days=1)

            if not need_dates:
                continue

            url = f"{base_url}/{start_date.isoformat()}..{today.isoformat()}?from={currency}&to=CNY"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                errors.append(f"Failed to fetch {currency}: {exc}")
                continue

            rates = data.get("rates", {})
            total_fetched += len(rates)

            for date_str, rate_dict in rates.items():
                rate_date = date.fromisoformat(date_str)
                cny_value = rate_dict.get("CNY")
                if cny_value is None:
                    continue

                if (currency, rate_date) in existing:
                    continue

                rate_to_rmb = Decimal(str(cny_value))

                # Upsert: check if exists (could have been inserted by another concurrent call)
                check = await db.execute(
                    select(ExchangeRate).where(
                        and_(
                            ExchangeRate.date == rate_date,
                            ExchangeRate.currency == currency,
                        )
                    )
                )
                ex = check.scalar_one_or_none()
                if ex:
                    ex.rate_to_rmb = rate_to_rmb
                else:
                    db.add(
                        ExchangeRate(
                            date=rate_date,
                            currency=currency,
                            rate_to_rmb=rate_to_rmb,
                        )
                    )
                total_inserted += 1
                existing.add((currency, rate_date))

    await db.commit()

    return {
        "currencies": CURRENCIES,
        "date_range": f"{start_date.isoformat()} to {today.isoformat()}",
        "rates_fetched": total_fetched,
        "rates_upserted": total_inserted,
        "errors": errors,
    }


@router.post("/fx")
async def trigger_fx_sync(
    days_back: int = 90,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger exchange rate sync from frankfurter.app."""
    summary = await sync_exchange_rates(db, days_back=days_back)
    return summary
