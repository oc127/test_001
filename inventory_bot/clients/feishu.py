"""Feishu bot client — sends interactive cards to a group chat via webhook.

Why this exists
---------------
The user and team live inside 飞书 (Feishu). Email alerts are ignored;
in-app message cards are read. We push weekly reorder alerts, data quality
warnings, and price spikes to a bot chat so the buyer sees them the moment
they open 飞书.

Two transport modes are supported:
1. **Custom bot webhook** (simplest): no OAuth, POST a JSON body to the URL
   shown in the group chat's bot config. Supports signed requests.
2. **Tenant access token** (richer): send direct messages to individuals,
   @mention people inside a card, thread replies. Used later when we move
   beyond one group chat.

This module sticks to the webhook for the MVP. Interactive cards are
composed using the Feishu card JSON format (see reference:
https://open.feishu.cn/document/ukTMukTMukTM/uczM3QjL3MzN04yNzcDN).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import httpx

from ..config import SETTINGS

logger = logging.getLogger(__name__)


# Feishu card colour themes
COLOUR_RED = "red"
COLOUR_ORANGE = "orange"
COLOUR_YELLOW = "yellow"
COLOUR_GREEN = "green"
COLOUR_BLUE = "blue"
COLOUR_GREY = "grey"
COLOUR_TURQUOISE = "turquoise"


@dataclass
class CardElement:
    """Simple wrapper for card element dicts so call sites read cleanly."""
    payload: dict

    def to_dict(self) -> dict:
        return self.payload


def _sign(secret: str, timestamp: int) -> str:
    """Generate the Feishu webhook signature."""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _text_element(content: str, tag: str = "lark_md") -> dict:
    """Create a markdown text element."""
    return {"tag": "div", "text": {"tag": tag, "content": content}}


def _divider() -> dict:
    return {"tag": "hr"}


def _note(contents: list[str]) -> dict:
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": c} for c in contents],
    }


def _action_button(text: str, url: str, type_: str = "default") -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": type_,  # default / primary / danger
        "url": url,
    }


def _action_row(buttons: list[dict]) -> dict:
    return {"tag": "action", "actions": buttons}


def _fields(cols: list[tuple[str, str]]) -> dict:
    """Two-column label/value field block."""
    return {
        "tag": "div",
        "fields": [
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"},
            }
            for label, value in cols
        ],
    }


def build_card(
    title: str,
    elements: list[dict],
    colour: str = COLOUR_BLUE,
    subtitle: Optional[str] = None,
) -> dict:
    """Assemble a Feishu interactive card payload."""
    header = {
        "title": {"tag": "plain_text", "content": title},
        "template": colour,
    }
    if subtitle:
        header["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return {
        "config": {"wide_screen_mode": True},
        "header": header,
        "elements": elements,
    }


# ===== Specific card templates =====

def build_weekly_alert_card(
    urgent_count: int,
    planned_count: int,
    dead_stock_count: int,
    overstock_count: int,
    total_urgent_value_rmb: float,
    report_url: Optional[str] = None,
    bitable_url: Optional[str] = None,
    week_of: Optional[date] = None,
) -> dict:
    """Summary card for the Monday 9am weekly alert."""
    week_of = week_of or date.today()
    elements: list[dict] = [
        _text_element(
            f"📅 本周库存扫描 | {week_of.strftime('%Y-%m-%d')}\n"
            f"共发现需要关注的 SKU。请对照下方动作清单处理。"
        ),
        _divider(),
        _fields(
            [
                ("🔴 紧急补货", f"**{urgent_count}** 个 SKU"),
                ("🟡 计划补货", f"**{planned_count}** 个 SKU"),
                ("🟤 死库存", f"**{dead_stock_count}** 个 SKU"),
                ("📦 积压警告", f"**{overstock_count}** 个 SKU"),
            ]
        ),
        _divider(),
        _text_element(
            f"**预计紧急补货金额**: ¥{total_urgent_value_rmb:,.0f}\n\n"
            f"👉 点击下方按钮查看详情和提交审批。"
        ),
    ]
    buttons: list[dict] = []
    if report_url:
        buttons.append(_action_button("查看完整报告", report_url, type_="primary"))
    if bitable_url:
        buttons.append(_action_button("打开多维表格", bitable_url))
    if buttons:
        elements.append(_action_row(buttons))
    elements.append(
        _note([f"自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  inventory_bot"])
    )

    colour = COLOUR_RED if urgent_count > 0 else COLOUR_YELLOW if planned_count > 0 else COLOUR_GREEN
    return build_card(
        title=f"本周补货提醒 · {urgent_count} 急单待处理",
        subtitle=f"工艺品 lead 60天 | 3C lead 20天",
        elements=elements,
        colour=colour,
    )


def build_urgent_sku_card(
    sku: str,
    product_name: str,
    status: str,
    days_of_cover: float,
    total_available: int,
    recommended_qty: int,
    recommended_date: date,
    expected_arrival: date,
    estimated_value_rmb: Optional[float],
    notes: list[str],
    confirm_url: Optional[str] = None,
) -> dict:
    """Per-SKU detailed card — used when sending highest-priority items individually."""
    lines = [
        f"**{product_name}**",
        f"SKU: `{sku}`",
        f"",
        f"🕐 **剩余天数**: {days_of_cover:.0f} 天 · 总可用: {total_available}",
        f"📦 **建议订购**: {recommended_qty} 件",
        f"📅 **建议下单**: {recommended_date.strftime('%m-%d')}",
        f"🚚 **预计到货**: {expected_arrival.strftime('%m-%d')}",
    ]
    if estimated_value_rmb:
        lines.append(f"💰 **预估金额**: ¥{estimated_value_rmb:,.0f}")

    elements: list[dict] = [_text_element("\n".join(lines))]

    if notes:
        elements.append(_divider())
        elements.append(_text_element("**⚠️ 注意事项**:\n" + "\n".join(f"- {n}" for n in notes)))

    if confirm_url:
        elements.append(_divider())
        elements.append(
            _action_row(
                [
                    _action_button("✅ 确认下单", confirm_url + "&action=confirm", type_="primary"),
                    _action_button("🔄 调整数量", confirm_url + "&action=edit"),
                    _action_button("❌ 取消", confirm_url + "&action=cancel", type_="danger"),
                ]
            )
        )

    return build_card(
        title=f"{status} {sku}",
        subtitle=product_name,
        elements=elements,
        colour=COLOUR_RED if "急" in status else COLOUR_ORANGE,
    )


def build_data_quality_card(
    error_count: int,
    warning_count: int,
    info_count: int,
    top_issues: list[str],
    bitable_url: Optional[str] = None,
) -> dict:
    """Card used when data quality issues block the weekly run."""
    elements: list[dict] = [
        _text_element(
            "🛑 **数据质量问题 — 暂停补货计算**\n\n"
            "发现以下问题，请助理先确认/修正，再重新运行扫描。"
        ),
        _divider(),
        _fields(
            [
                ("错误", f"**{error_count}** 条"),
                ("警告", f"**{warning_count}** 条"),
                ("提示", f"**{info_count}** 条"),
            ]
        ),
    ]
    if top_issues:
        elements.append(_divider())
        elements.append(
            _text_element("**Top 问题**:\n" + "\n".join(f"{i + 1}. {msg}" for i, msg in enumerate(top_issues[:10])))
        )
    if bitable_url:
        elements.append(
            _action_row([_action_button("去多维表格修正", bitable_url, type_="primary")])
        )

    return build_card(
        title="⚠️ 数据质量检查失败",
        elements=elements,
        colour=COLOUR_ORANGE,
    )


def build_price_alert_card(alerts: list[dict]) -> dict:
    """Alerts when 1688 supplier prices have changed."""
    elements: list[dict] = [
        _text_element(f"💱 **{len(alerts)} 个供应商价格变动**")
    ]
    for a in alerts[:10]:
        direction = "📈" if a["change_direction"] == "up" else "📉"
        elements.append(_divider())
        elements.append(
            _text_element(
                f"{direction} **{a['sku']}** ({a['supplier']})\n"
                f"¥{a['old_price_rmb']} → ¥{a['new_price_rmb']} "
                f"({a['change_pct']:+.1f}%)\n"
                f"[查看]({a['url']})"
            )
        )
    return build_card(
        title="💸 供应商价格变动",
        elements=elements,
        colour=COLOUR_TURQUOISE,
    )


def build_monthly_review_card(
    month: str,
    total_ordered_value_rmb: float,
    stockout_count: int,
    overstock_rmb_impact: float,
    top_unreliable_suppliers: list[str],
    report_url: Optional[str] = None,
) -> dict:
    """Monthly retrospective card."""
    elements: list[dict] = [
        _text_element(f"📊 **{month} 月度复盘**"),
        _divider(),
        _fields(
            [
                ("本月采购总额", f"¥{total_ordered_value_rmb:,.0f}"),
                ("断货次数", f"{stockout_count} 次"),
                ("积压占用资金", f"¥{overstock_rmb_impact:,.0f}"),
                ("问题供应商", f"{len(top_unreliable_suppliers)} 家"),
            ]
        ),
    ]
    if top_unreliable_suppliers:
        elements.append(_divider())
        elements.append(
            _text_element(
                "**需要约谈的供应商**:\n"
                + "\n".join(f"- {s}" for s in top_unreliable_suppliers[:5])
            )
        )
    if report_url:
        elements.append(
            _action_row([_action_button("查看完整月报", report_url, type_="primary")])
        )
    return build_card(
        title=f"📈 {month} 月度复盘",
        elements=elements,
        colour=COLOUR_BLUE,
    )


# ===== Transport =====

class FeishuBot:
    """Thin wrapper around the Feishu custom bot webhook."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        secret: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self.webhook_url = webhook_url or SETTINGS.feishu_bot_webhook
        self.secret = secret or SETTINGS.feishu_bot_secret
        self.timeout = timeout

    def _signed_body(self, body: dict) -> dict:
        if not self.secret:
            return body
        ts = int(time.time())
        return {
            **body,
            "timestamp": str(ts),
            "sign": _sign(self.secret, ts),
        }

    def send_card(self, card: dict) -> dict:
        """POST an interactive card to the webhook."""
        if not self.webhook_url:
            logger.warning("FEISHU_BOT_WEBHOOK not configured — dropping card")
            return {"ok": False, "reason": "no_webhook_configured", "card": card}

        body = self._signed_body({"msg_type": "interactive", "card": card})
        try:
            resp = httpx.post(self.webhook_url, json=body, timeout=self.timeout)
            data = resp.json()
        except Exception as exc:  # network or JSON error
            logger.exception("Feishu webhook POST failed: %s", exc)
            return {"ok": False, "reason": "network_error", "error": str(exc)}

        if data.get("StatusCode", 0) != 0 and data.get("code", 0) != 0:
            logger.warning("Feishu webhook non-zero status: %s", data)
            return {"ok": False, "response": data}
        return {"ok": True, "response": data}

    def send_text(self, content: str) -> dict:
        """Simple text message — for debug/ping."""
        if not self.webhook_url:
            logger.warning("FEISHU_BOT_WEBHOOK not configured — dropping text")
            return {"ok": False}
        body = self._signed_body({"msg_type": "text", "content": {"text": content}})
        try:
            resp = httpx.post(self.webhook_url, json=body, timeout=self.timeout)
            return {"ok": True, "response": resp.json()}
        except Exception as exc:
            logger.exception("Feishu webhook text POST failed: %s", exc)
            return {"ok": False, "error": str(exc)}


def dump_card_preview(card: dict) -> str:
    """Return a JSON preview — used when running in --dry-run mode."""
    return json.dumps(card, ensure_ascii=False, indent=2)
