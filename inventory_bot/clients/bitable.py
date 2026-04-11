"""Feishu Bitable (多维表格) API client.

Why this exists
---------------
Bitable IS the user's database. The assistant edits product master data,
updates inventory, and adds sales records inside Bitable. The bot reads
those tables, runs the reorder engine, and writes results BACK to a
"decisions" table so the buyer sees them inline.

Bitable API reference:
  https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list

This client exposes only the operations we need:
  - list_records(table_name, filter=None) -> list[dict]
  - batch_create_records(table_name, records)
  - batch_update_records(table_name, updates)
  - delete_records(table_name, record_ids)
  - upsert_records(table_name, records, unique_field)

Auth: tenant_access_token, refreshed on expiry.

IMPORTANT design notes
----------------------
1. Field names use Chinese column headers (see `config.BitableTables` and
   docs/bitable_schema.md). The client maps logical names from our models
   to Bitable field names via a mapping dict per table.
2. Bitable date fields are **millisecond** timestamps (not ISO strings).
3. Rate limit: 20 requests/sec per app. We batch writes in groups of 500.
4. The client is read-optimistic: a record that exists in Bitable but is
   missing a field returns None for that field. We don't fail.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

import httpx

from ..config import SETTINGS, BitableTables
from ..models import (
    ForecastRecord,
    InventorySnapshot,
    Product,
    PurchaseOrder,
    SalesRecord,
)

logger = logging.getLogger(__name__)

FEISHU_HOST = "https://open.feishu.cn"
BATCH_SIZE = 500  # Bitable batch limit


# ===== Field mappings =====
# Bitable column name (Chinese) <-> model attribute name

PRODUCT_FIELDS = {
    "SKU": "sku",
    "产品名称": "name",
    "产品线": "product_line",
    "生产周期天数": "production_lead_days",
    "运输周期天数": "shipping_lead_days",
    "安全库存天数": "safety_stock_days",
    "默认供应商": "default_supplier",
    "1688链接": "supplier_1688_url",
    "MOQ": "moq",
    "单价RMB": "unit_cost_rmb",
    "备注": "notes",
}

SALES_FIELDS = {
    "SKU": "sku",
    "日期": "date",
    "销量": "units_sold",
    "店铺": "store",
}

FORECAST_FIELDS = {
    "SKU": "sku",
    "日期": "date",
    "预估销量": "forecast_units",
    "来源": "source",
}

INVENTORY_FIELDS = {
    "SKU": "sku",
    "快照日期": "snapshot_date",
    "在库数量": "qty_in_stock",
    "在途数量": "qty_in_transit",
    "在产数量": "qty_in_production",
    "店铺": "store",
}

PO_FIELDS = {
    "PO编号": "po_number",
    "SKU": "sku",
    "下单日期": "order_date",
    "数量": "qty",
    "单价RMB": "unit_price_rmb",
    "供应商": "supplier",
    "状态": "status",
    "预计生产完成": "expected_production_done",
    "预计到货日期": "expected_arrival_date",
    "实际到货日期": "actual_arrival_date",
    "备注": "notes",
}

DECISION_FIELDS = {
    "SKU": "sku",
    "产品名称": "product_name",
    "状态": "status",
    "剩余天数": "days_of_cover_remaining",
    "可用总量": "total_available",
    "建议订购量": "recommended_order_qty",
    "建议下单日期": "recommended_order_date",
    "预计到货日期": "expected_arrival_date",
    "预估金额RMB": "estimated_order_value_rmb",
    "紧急度分数": "urgency_score",
    "生成时间": "generated_at",
    "备注": "notes",
    "决策": "decision",  # buyer fills this in: 已下单 / 调整 / 取消
}


# ===== Helpers =====

def _date_to_ms(d: date | datetime | None) -> Optional[int]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return int(d.timestamp() * 1000)
    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)


def _ms_to_date(ms: Any) -> Optional[date]:
    if ms in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000).date()
    except (ValueError, TypeError):
        return None


def _to_bitable_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return _date_to_ms(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_to_bitable_value(v) for v in value]
    return value


def _fields_to_bitable(model_dict: dict, mapping: dict[str, str]) -> dict:
    """Convert {attr_name: value} → {Bitable Field Name: value}."""
    out = {}
    for col_name, attr_name in mapping.items():
        if attr_name in model_dict:
            out[col_name] = _to_bitable_value(model_dict[attr_name])
    return out


def _fields_from_bitable(bitable_fields: dict, mapping: dict[str, str]) -> dict:
    """Convert {Bitable Field Name: value} → {attr_name: value}."""
    out = {}
    for col_name, attr_name in mapping.items():
        if col_name in bitable_fields:
            out[attr_name] = bitable_fields[col_name]
    return out


# ===== Client =====

class BitableClient:
    """Read/write access to Feishu Bitable tables."""

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        app_token: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.app_id = app_id or SETTINGS.feishu_app_id
        self.app_secret = app_secret or SETTINGS.feishu_app_secret
        self.app_token = app_token or SETTINGS.bitable_app_token
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._table_id_cache: dict[str, str] = {}
        self._http = httpx.Client(timeout=timeout)

    # ----- Auth -----

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token
        resp = self._http.post(
            f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {data}")
        self._access_token = data["tenant_access_token"]
        self._token_expires_at = now + int(data.get("expire", 7200))
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    # ----- Table resolution -----

    def _resolve_table_id(self, table_name: str) -> str:
        """Find a table's internal ID by its display name."""
        if table_name in self._table_id_cache:
            return self._table_id_cache[table_name]
        url = (
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{self.app_token}/tables"
        )
        resp = self._http.get(url, headers=self._headers(), params={"page_size": 100})
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"List tables failed: {data}")
        for tb in data["data"]["items"]:
            self._table_id_cache[tb["name"]] = tb["table_id"]
        if table_name not in self._table_id_cache:
            raise KeyError(f"Bitable table not found: {table_name}")
        return self._table_id_cache[table_name]

    # ----- Read -----

    def list_records(
        self,
        table_name: str,
        filter_str: Optional[str] = None,
        page_size: int = 500,
    ) -> list[dict]:
        """Return all records (all pages) in a table, optionally filtered."""
        table_id = self._resolve_table_id(table_name)
        url = (
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{self.app_token}"
            f"/tables/{table_id}/records"
        )
        all_rows: list[dict] = []
        page_token: Optional[str] = None
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            if filter_str:
                params["filter"] = filter_str
            resp = self._http.get(url, headers=self._headers(), params=params)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"List records failed ({table_name}): {data}")
            items = data["data"].get("items", [])
            for it in items:
                all_rows.append(
                    {
                        "record_id": it["record_id"],
                        "fields": it.get("fields", {}),
                    }
                )
            if not data["data"].get("has_more"):
                break
            page_token = data["data"].get("page_token")
            if not page_token:
                break
            time.sleep(0.05)  # gentle rate limit
        return all_rows

    # ----- Write -----

    def batch_create_records(self, table_name: str, records: list[dict]) -> list[dict]:
        """Create records in batches of BATCH_SIZE. Returns created rows."""
        if not records:
            return []
        table_id = self._resolve_table_id(table_name)
        url = (
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{self.app_token}"
            f"/tables/{table_id}/records/batch_create"
        )
        results: list[dict] = []
        for i in range(0, len(records), BATCH_SIZE):
            chunk = records[i : i + BATCH_SIZE]
            body = {"records": [{"fields": r} for r in chunk]}
            resp = self._http.post(url, headers=self._headers(), json=body)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Batch create failed ({table_name}): {data}")
            results.extend(data["data"]["records"])
            time.sleep(0.1)
        return results

    def batch_update_records(self, table_name: str, updates: list[dict]) -> list[dict]:
        """updates = list of {record_id, fields}."""
        if not updates:
            return []
        table_id = self._resolve_table_id(table_name)
        url = (
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{self.app_token}"
            f"/tables/{table_id}/records/batch_update"
        )
        results: list[dict] = []
        for i in range(0, len(updates), BATCH_SIZE):
            chunk = updates[i : i + BATCH_SIZE]
            body = {"records": chunk}
            resp = self._http.post(url, headers=self._headers(), json=body)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Batch update failed ({table_name}): {data}")
            results.extend(data["data"]["records"])
            time.sleep(0.1)
        return results

    def batch_delete_records(self, table_name: str, record_ids: list[str]) -> None:
        if not record_ids:
            return
        table_id = self._resolve_table_id(table_name)
        url = (
            f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{self.app_token}"
            f"/tables/{table_id}/records/batch_delete"
        )
        for i in range(0, len(record_ids), BATCH_SIZE):
            chunk = record_ids[i : i + BATCH_SIZE]
            resp = self._http.post(
                url, headers=self._headers(), json={"records": chunk}
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Batch delete failed ({table_name}): {data}")
            time.sleep(0.1)

    def upsert_by_field(
        self,
        table_name: str,
        records: list[dict],
        unique_field: str,
    ) -> None:
        """Update existing records by unique_field, create missing ones."""
        existing = self.list_records(table_name)
        existing_by_key: dict[Any, str] = {
            r["fields"].get(unique_field): r["record_id"] for r in existing
        }
        to_update: list[dict] = []
        to_create: list[dict] = []
        for rec in records:
            key = rec.get(unique_field)
            if key in existing_by_key:
                to_update.append({"record_id": existing_by_key[key], "fields": rec})
            else:
                to_create.append(rec)
        if to_update:
            self.batch_update_records(table_name, to_update)
        if to_create:
            self.batch_create_records(table_name, to_create)


# ===== High-level typed repositories =====

class InventoryBotRepository:
    """Typed wrapper: Bitable <-> our dataclass models."""

    def __init__(self, client: Optional[BitableClient] = None):
        self.client = client or BitableClient()

    # ----- Products -----

    def fetch_products(self) -> list[Product]:
        rows = self.client.list_records(BitableTables.PRODUCTS)
        products: list[Product] = []
        for row in rows:
            mapped = _fields_from_bitable(row["fields"], PRODUCT_FIELDS)
            if not mapped.get("sku"):
                continue
            products.append(
                Product(
                    sku=str(mapped["sku"]),
                    name=str(mapped.get("name", "")),
                    product_line=str(mapped.get("product_line", "工艺品")),
                    production_lead_days=int(mapped.get("production_lead_days") or 30),
                    shipping_lead_days=int(mapped.get("shipping_lead_days") or 30),
                    safety_stock_days=int(mapped.get("safety_stock_days") or 14),
                    default_supplier=mapped.get("default_supplier"),
                    supplier_1688_url=mapped.get("supplier_1688_url"),
                    moq=int(mapped.get("moq") or 300),
                    unit_cost_rmb=(
                        Decimal(str(mapped["unit_cost_rmb"]))
                        if mapped.get("unit_cost_rmb") not in (None, "")
                        else None
                    ),
                    notes=str(mapped.get("notes", "")),
                )
            )
        return products

    # ----- Sales history -----

    def fetch_sales_history(self) -> list[SalesRecord]:
        rows = self.client.list_records(BitableTables.SALES_HISTORY)
        records: list[SalesRecord] = []
        for row in rows:
            m = _fields_from_bitable(row["fields"], SALES_FIELDS)
            sku = m.get("sku")
            d = _ms_to_date(m.get("date"))
            if not sku or not d:
                continue
            records.append(
                SalesRecord(
                    sku=str(sku),
                    date=d,
                    units_sold=int(m.get("units_sold") or 0),
                    store=m.get("store"),
                )
            )
        return records

    # ----- Forecasts -----

    def fetch_forecasts(self) -> list[ForecastRecord]:
        rows = self.client.list_records(BitableTables.SALES_FORECAST)
        out: list[ForecastRecord] = []
        for row in rows:
            m = _fields_from_bitable(row["fields"], FORECAST_FIELDS)
            sku = m.get("sku")
            d = _ms_to_date(m.get("date"))
            if not sku or not d:
                continue
            out.append(
                ForecastRecord(
                    sku=str(sku),
                    date=d,
                    forecast_units=int(m.get("forecast_units") or 0),
                    source=str(m.get("source") or "manual"),
                )
            )
        return out

    def write_forecasts(self, forecasts: list[ForecastRecord]) -> None:
        """Overwrite all forecasts with our computed baseline + manual overrides."""
        rows = [
            _fields_to_bitable(
                {
                    "sku": f.sku,
                    "date": f.date,
                    "forecast_units": f.forecast_units,
                    "source": f.source,
                },
                FORECAST_FIELDS,
            )
            for f in forecasts
        ]
        # Simplest semantics: clear existing "baseline" rows, re-create them.
        # Real client should do this more carefully.
        existing = self.client.list_records(BitableTables.SALES_FORECAST)
        baseline_ids = [
            r["record_id"]
            for r in existing
            if r["fields"].get("来源") == "baseline"
        ]
        if baseline_ids:
            self.client.batch_delete_records(BitableTables.SALES_FORECAST, baseline_ids)
        self.client.batch_create_records(BitableTables.SALES_FORECAST, rows)

    # ----- Inventory -----

    def fetch_latest_inventory(self) -> dict[str, InventorySnapshot]:
        """Return most recent snapshot per SKU."""
        rows = self.client.list_records(BitableTables.INVENTORY_SNAPSHOTS)
        latest: dict[str, InventorySnapshot] = {}
        for row in rows:
            m = _fields_from_bitable(row["fields"], INVENTORY_FIELDS)
            sku = m.get("sku")
            snap_date = _ms_to_date(m.get("snapshot_date"))
            if not sku or not snap_date:
                continue
            snap = InventorySnapshot(
                sku=str(sku),
                snapshot_date=snap_date,
                qty_in_stock=int(m.get("qty_in_stock") or 0),
                qty_in_transit=int(m.get("qty_in_transit") or 0),
                qty_in_production=int(m.get("qty_in_production") or 0),
                store=m.get("store"),
            )
            if sku not in latest or snap_date > latest[sku].snapshot_date:
                latest[sku] = snap
        return latest

    def fetch_previous_inventory(
        self, current_latest: dict[str, InventorySnapshot]
    ) -> dict[str, InventorySnapshot]:
        """Return the 2nd-most-recent snapshot per SKU — used for swing detection."""
        rows = self.client.list_records(BitableTables.INVENTORY_SNAPSHOTS)
        per_sku: dict[str, list[InventorySnapshot]] = {}
        for row in rows:
            m = _fields_from_bitable(row["fields"], INVENTORY_FIELDS)
            sku = m.get("sku")
            snap_date = _ms_to_date(m.get("snapshot_date"))
            if not sku or not snap_date:
                continue
            per_sku.setdefault(str(sku), []).append(
                InventorySnapshot(
                    sku=str(sku),
                    snapshot_date=snap_date,
                    qty_in_stock=int(m.get("qty_in_stock") or 0),
                    qty_in_transit=int(m.get("qty_in_transit") or 0),
                    qty_in_production=int(m.get("qty_in_production") or 0),
                    store=m.get("store"),
                )
            )
        previous: dict[str, InventorySnapshot] = {}
        for sku, snaps in per_sku.items():
            snaps.sort(key=lambda s: s.snapshot_date, reverse=True)
            if len(snaps) >= 2:
                previous[sku] = snaps[1]
        return previous

    # ----- Purchase orders -----

    def fetch_purchase_orders(self) -> list[PurchaseOrder]:
        rows = self.client.list_records(BitableTables.PURCHASE_ORDERS)
        pos: list[PurchaseOrder] = []
        for row in rows:
            m = _fields_from_bitable(row["fields"], PO_FIELDS)
            if not m.get("po_number") or not m.get("sku"):
                continue
            order_date = _ms_to_date(m.get("order_date"))
            if not order_date:
                continue
            pos.append(
                PurchaseOrder(
                    po_number=str(m["po_number"]),
                    sku=str(m["sku"]),
                    order_date=order_date,
                    qty=int(m.get("qty") or 0),
                    unit_price_rmb=(
                        Decimal(str(m["unit_price_rmb"]))
                        if m.get("unit_price_rmb") not in (None, "")
                        else None
                    ),
                    supplier=m.get("supplier"),
                    status=str(m.get("status") or "draft"),
                    expected_production_done=_ms_to_date(m.get("expected_production_done")),
                    expected_arrival_date=_ms_to_date(m.get("expected_arrival_date")),
                    actual_arrival_date=_ms_to_date(m.get("actual_arrival_date")),
                    notes=str(m.get("notes", "")),
                )
            )
        return pos

    def create_draft_pos(self, draft_pos: list[PurchaseOrder]) -> None:
        rows = [
            _fields_to_bitable(
                {
                    "po_number": p.po_number,
                    "sku": p.sku,
                    "order_date": p.order_date,
                    "qty": p.qty,
                    "unit_price_rmb": p.unit_price_rmb,
                    "supplier": p.supplier,
                    "status": p.status,
                    "expected_arrival_date": p.expected_arrival_date,
                    "notes": p.notes,
                },
                PO_FIELDS,
            )
            for p in draft_pos
        ]
        self.client.batch_create_records(BitableTables.PURCHASE_ORDERS, rows)

    # ----- Decisions log -----

    def write_decisions(self, recommendations: list[dict]) -> None:
        """Push this week's recommendations into the decisions log table."""
        rows = []
        now = datetime.now()
        for rec in recommendations:
            rows.append(
                _fields_to_bitable(
                    {**rec, "generated_at": now},
                    DECISION_FIELDS,
                )
            )
        self.client.batch_create_records(BitableTables.DECISIONS_LOG, rows)
