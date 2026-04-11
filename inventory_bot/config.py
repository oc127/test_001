"""Central configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_IN_DIR = PROJECT_ROOT / "data_in"
DATA_OUT_DIR = PROJECT_ROOT / "data_out"
DOCS_DIR = PROJECT_ROOT / "docs"


def _env(key: str, default: Any = None) -> Any:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Feishu
    feishu_app_id: str = field(default_factory=lambda: _env("FEISHU_APP_ID", ""))
    feishu_app_secret: str = field(default_factory=lambda: _env("FEISHU_APP_SECRET", ""))
    bitable_app_token: str = field(default_factory=lambda: _env("BITABLE_APP_TOKEN", ""))
    feishu_bot_webhook: str = field(default_factory=lambda: _env("FEISHU_BOT_WEBHOOK", ""))
    feishu_bot_secret: str = field(default_factory=lambda: _env("FEISHU_BOT_SECRET", ""))

    # Firecrawl
    firecrawl_api_key: str = field(default_factory=lambda: _env("FIRECRAWL_API_KEY", ""))

    # ProfitLens (phase 2)
    profitlens_api_url: str = field(default_factory=lambda: _env("PROFITLENS_API_URL", ""))
    profitlens_api_token: str = field(default_factory=lambda: _env("PROFITLENS_API_TOKEN", ""))

    # Algorithm params
    default_safety_stock_days: int = field(default_factory=lambda: _env_int("DEFAULT_SAFETY_STOCK_DAYS", 14))
    sales_history_window_days: int = field(default_factory=lambda: _env_int("SALES_HISTORY_WINDOW_DAYS", 30))
    forecast_horizon_days: int = field(default_factory=lambda: _env_int("FORECAST_HORIZON_DAYS", 90))
    dead_stock_decline_threshold: float = field(default_factory=lambda: _env_float("DEAD_STOCK_DECLINE_THRESHOLD", 0.35))
    dead_stock_consecutive_weeks: int = field(default_factory=lambda: _env_int("DEAD_STOCK_CONSECUTIVE_WEEKS", 4))

    # Scheduling
    weekly_alert_day: str = field(default_factory=lambda: _env("WEEKLY_ALERT_DAY", "mon"))
    weekly_alert_hour: int = field(default_factory=lambda: _env_int("WEEKLY_ALERT_HOUR", 9))
    monthly_review_day: int = field(default_factory=lambda: _env_int("MONTHLY_REVIEW_DAY", 1))
    monthly_review_hour: int = field(default_factory=lambda: _env_int("MONTHLY_REVIEW_HOUR", 9))

    # Environment
    env: str = field(default_factory=lambda: _env("ENV", "development"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Asia/Shanghai"))


SETTINGS = Settings()


# ===== Product Line Defaults =====
# Lead times as specified by the user (user's actual business)
PRODUCT_LINE_DEFAULTS = {
    "工艺品": {
        "production_lead_days": 30,
        "shipping_lead_days": 30,
        "total_lead_days": 60,
        "chinese_new_year_extension_days": 30,  # factories shut for ~30 extra days around CNY
        "min_safety_stock_days": 14,
        "typical_moq": 300,
    },
    "3C": {
        "production_lead_days": 5,
        "shipping_lead_days": 15,
        "total_lead_days": 20,
        "chinese_new_year_extension_days": 15,
        "min_safety_stock_days": 10,
        "typical_moq": 500,
    },
}


# ===== Status thresholds =====
class ReorderStatus:
    URGENT = "🔴急单"          # coverage < lead_time: need to order NOW
    PLANNED = "🟡计划"          # coverage < lead_time + 7: plan this week
    SAFE = "🟢安全"             # plenty of coverage
    DEAD_STOCK = "🟤死库存"     # declining velocity; don't reorder
    OVERSTOCK = "📦积压"        # coverage > 180 days


# ===== Bitable table names =====
class BitableTables:
    PRODUCTS = "产品主数据"
    SALES_HISTORY = "销售历史"
    SALES_FORECAST = "销售预估"
    INVENTORY_SNAPSHOTS = "库存快照"
    PURCHASE_ORDERS = "采购订单"
    SUPPLIERS_1688 = "供应商1688"
    DECISIONS_LOG = "决策日志"
