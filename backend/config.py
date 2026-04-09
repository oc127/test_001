from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://profitlens:profitlens_dev@localhost:5432/profitlens"

    # Auth
    JWT_SECRET: str = "dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    # Exchange rate API (free, no key needed)
    FX_API_URL: str = "https://api.frankfurter.app"

    # Amazon marketplace constants
    MARKETPLACES: dict = {
        "US": {"id": "ATVPDKIKX0DER", "currency": "USD"},
        "CA": {"id": "A2EUQ1WTGCTBG2", "currency": "CAD"},
        "UK": {"id": "A1F83G8C2ARO7P", "currency": "GBP"},
        "DE": {"id": "A1PA6795UKMFR9", "currency": "EUR"},
        "FR": {"id": "A13V1IB3VIYZZH", "currency": "EUR"},
        "IT": {"id": "APJ6JRA9NG5V4", "currency": "EUR"},
        "ES": {"id": "A1RKKUPIHCS9HS", "currency": "EUR"},
        "JP": {"id": "A1VC38T7YXB528", "currency": "JPY"},
        "AU": {"id": "A39IBJ37TRP1C6", "currency": "AUD"},
    }

    # EU VAT standard rates (2026)
    VAT_RATES: dict = {
        "DE": 0.19,
        "FR": 0.20,
        "IT": 0.22,
        "ES": 0.21,
        "UK": 0.20,
    }

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
