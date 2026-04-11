"""China-specific holiday calendar for lead time adjustment.

Why this exists
---------------
Chinese manufacturing factories close for 2-4 weeks around Spring Festival
(Chinese New Year). No Western inventory tool knows about this. For sellers
sourcing from 1688 / Guangdong / Yiwu, missing this adjustment means
ordering too late and running out during peak season.

This module:
- Knows about CNY, Golden Week, Amazon peak seasons
- Given a base lead time and an order date, returns an adjusted lead time
- Explains WHY the adjustment was made (for the UI)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


# ===== Known dates through 2030 =====
# Spring Festival (Chinese New Year) — factory shutdown 15-30 days
# Dates from China's official holiday calendar
SPRING_FESTIVAL_DATES = {
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
}

# National Day (国庆节) — 7-day factory break Oct 1-7
NATIONAL_DAY_START = (10, 1)  # Month, day
NATIONAL_DAY_DURATION = 7

# China 618 — factory load spike early-mid June (mild impact)
CHINA_618_PEAK = (6, 18)

# China Double 11 (Nov 11) — heavy domestic load spike Nov 1-11
CHINA_DOUBLE_11_START = (11, 1)
CHINA_DOUBLE_11_END = (11, 11)


@dataclass
class LeadTimeAdjustment:
    base_lead_days: int
    adjusted_lead_days: int
    extra_days: int
    reason: str
    affects_order_window: tuple[date, date] | None = None

    @property
    def is_adjusted(self) -> bool:
        return self.extra_days > 0


def get_spring_festival(year: int) -> Optional[date]:
    """Return Spring Festival date for given year, or None if unknown."""
    return SPRING_FESTIVAL_DATES.get(year)


def _days_to_cny(order_date: date) -> tuple[Optional[int], Optional[date]]:
    """Return (days until next CNY, CNY date) or (None, None)."""
    for offset in (0, 1):
        cny = get_spring_festival(order_date.year + offset)
        if cny and cny >= order_date:
            return (cny - order_date).days, cny
    return None, None


def _is_national_day(d: date) -> bool:
    return d.month == NATIONAL_DAY_START[0] and NATIONAL_DAY_START[1] <= d.day <= NATIONAL_DAY_START[1] + NATIONAL_DAY_DURATION - 1


def _is_double_11_window(d: date) -> bool:
    return d.month == 11 and CHINA_DOUBLE_11_START[1] <= d.day <= CHINA_DOUBLE_11_END[1]


def adjust_lead_time(
    base_lead_days: int,
    order_date: date,
    product_line: str = "工艺品",
    cny_extension_days: int = 30,
) -> LeadTimeAdjustment:
    """Return an adjusted lead time accounting for Chinese factory holidays.

    Logic
    -----
    1. If the order's expected production/shipping window overlaps Spring Festival,
       extend by `cny_extension_days` (default 30 for handicrafts, typically shorter
       for 3C because reopening is faster).
    2. If it overlaps National Day (Oct 1-7), add 7 days.
    3. If it overlaps China Double-11 peak (Nov 1-11) AND product line is 3C
       (domestic component supply pressure), add 5 days.
    4. Otherwise no adjustment.

    The "production/shipping window" is naively defined as [order_date, order_date + base_lead_days].
    """
    arrival = order_date + timedelta(days=base_lead_days)

    # --- Check Spring Festival overlap ---
    days_to_cny, cny_date = _days_to_cny(order_date)
    if cny_date is not None:
        # Factory affected window: 15 days before CNY to 30 days after
        shutdown_start = cny_date - timedelta(days=15)
        shutdown_end = cny_date + timedelta(days=cny_extension_days)
        # If our production window touches the shutdown window, adjust
        if not (arrival < shutdown_start or order_date > shutdown_end):
            extra = cny_extension_days
            reason = (
                f"跨越春节 ({cny_date.strftime('%Y-%m-%d')})，"
                f"工厂停工约 {cny_extension_days} 天，lead time 延长"
            )
            return LeadTimeAdjustment(
                base_lead_days=base_lead_days,
                adjusted_lead_days=base_lead_days + extra,
                extra_days=extra,
                reason=reason,
                affects_order_window=(shutdown_start, shutdown_end),
            )

    # --- Check National Day overlap ---
    national_day = date(order_date.year, NATIONAL_DAY_START[0], NATIONAL_DAY_START[1])
    nd_end = national_day + timedelta(days=NATIONAL_DAY_DURATION - 1)
    if not (arrival < national_day or order_date > nd_end):
        extra = NATIONAL_DAY_DURATION
        return LeadTimeAdjustment(
            base_lead_days=base_lead_days,
            adjusted_lead_days=base_lead_days + extra,
            extra_days=extra,
            reason=f"跨越国庆节 (10月1-7)，工厂放假 7 天",
            affects_order_window=(national_day, nd_end),
        )

    # --- Check Double-11 domestic squeeze (3C only) ---
    if product_line == "3C":
        d11_start = date(order_date.year, 11, CHINA_DOUBLE_11_START[1])
        d11_end = date(order_date.year, 11, CHINA_DOUBLE_11_END[1])
        if not (arrival < d11_start or order_date > d11_end):
            extra = 5
            return LeadTimeAdjustment(
                base_lead_days=base_lead_days,
                adjusted_lead_days=base_lead_days + extra,
                extra_days=extra,
                reason="跨越双11国内大促，3C 零部件供应紧张，lead time 延长",
                affects_order_window=(d11_start, d11_end),
            )

    return LeadTimeAdjustment(
        base_lead_days=base_lead_days,
        adjusted_lead_days=base_lead_days,
        extra_days=0,
        reason="无节假日影响",
    )


# ===== Amazon peak season safety stock multiplier =====
# If your lead time completes during Amazon peak, you need MORE safety stock
# because demand surges during these events.
AMAZON_PEAKS = [
    ("Prime Day", (7, 10), (7, 20)),          # Mid-July
    ("Black Friday", (11, 20), (11, 30)),     # End Nov
    ("Cyber Monday", (12, 1), (12, 5)),       # Early Dec
    ("Christmas", (12, 15), (12, 31)),        # Late Dec
]


def amazon_peak_multiplier(target_date: date) -> tuple[float, str]:
    """Return (safety stock multiplier, reason) if date falls in an Amazon peak.

    During peak season, boost safety stock by 1.5x to absorb demand surges.
    """
    for name, start_md, end_md in AMAZON_PEAKS:
        start = date(target_date.year, *start_md)
        end = date(target_date.year, *end_md)
        if start <= target_date <= end:
            return 1.5, f"{name} 旺季，安全库存 ×1.5"
    return 1.0, ""
