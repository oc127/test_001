"""Sales forecast baseline generator.

Why this exists
---------------
Expecting an assistant to hand-type 200 SKUs × 90 days of sales forecast is
absurd. This module generates a reasonable baseline automatically:

1. Start with the 30-day trailing average for each SKU
2. Apply day-of-week weights (weekends usually sell more on Amazon)
3. Apply a linear trend (is the SKU accelerating or decelerating?)
4. Apply Amazon peak-season multipliers (Prime Day, BFCM, Christmas)

Note: Chinese holidays (春节/国庆/双11) do NOT affect demand for cross-border
Amazon FBA sellers — the inventory is already in Amazon's US/EU warehouses
and Amazon ships directly to the end customer. Chinese holidays only affect
the SUPPLY side (factory shutdowns extending lead time), which is handled
separately in core.holiday_calendar.adjust_lead_time.

The assistant's job becomes: **review and override specific dates** for known
events (promotions, new listings, ad campaigns), not type everything.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, stdev
from typing import Optional

from ..models import ForecastRecord, SalesRecord
from .holiday_calendar import amazon_peak_multiplier


# Weekend boost — Amazon sales typically ~15% higher on Sat/Sun
# Index: Mon=0, Sun=6
DEFAULT_WEEKDAY_WEIGHTS = [
    0.95,  # Mon
    0.95,  # Tue
    0.95,  # Wed
    1.00,  # Thu
    1.05,  # Fri
    1.15,  # Sat
    1.15,  # Sun
]


def compute_weekday_weights(history: list[SalesRecord]) -> list[float]:
    """Learn weekday weights from history. Returns default if insufficient data."""
    if len(history) < 21:  # need at least 3 weeks
        return DEFAULT_WEEKDAY_WEIGHTS[:]

    by_weekday: dict[int, list[int]] = defaultdict(list)
    for rec in history:
        by_weekday[rec.date.weekday()].append(rec.units_sold)

    averages = []
    for wd in range(7):
        samples = by_weekday.get(wd, [])
        averages.append(mean(samples) if samples else 0.0)

    overall = mean(averages) if averages else 0.0
    if overall <= 0:
        return DEFAULT_WEEKDAY_WEIGHTS[:]

    weights = [a / overall for a in averages]
    # Smooth: blend 70% learned + 30% default to avoid overfitting
    return [0.7 * w + 0.3 * d for w, d in zip(weights, DEFAULT_WEEKDAY_WEIGHTS)]


def compute_trend_slope(history: list[SalesRecord], window_days: int = 30) -> float:
    """Simple linear trend: daily rate of change over the window.

    Returns 0 if not enough data. Positive = accelerating, negative = decelerating.
    Uses least-squares but simplified for clarity.
    """
    if len(history) < 14:
        return 0.0

    cutoff = max(r.date for r in history) - timedelta(days=window_days)
    recent = [r for r in history if r.date >= cutoff]
    if len(recent) < 7:
        return 0.0

    # Split into halves, compare means
    recent.sort(key=lambda r: r.date)
    half = len(recent) // 2
    first_half = recent[:half]
    second_half = recent[half:]
    first_avg = mean(r.units_sold for r in first_half)
    second_avg = mean(r.units_sold for r in second_half)
    days_diff = (second_half[len(second_half) // 2].date - first_half[len(first_half) // 2].date).days
    if days_diff <= 0:
        return 0.0
    return (second_avg - first_avg) / days_diff


def trailing_average(history: list[SalesRecord], window_days: int = 30) -> float:
    """Average daily sales over the last `window_days` days."""
    if not history:
        return 0.0
    cutoff = max(r.date for r in history) - timedelta(days=window_days)
    recent = [r.units_sold for r in history if r.date >= cutoff]
    if not recent:
        return 0.0
    # Sum divided by window days (not len), to reflect "per day of window"
    return sum(recent) / window_days


def daily_sales_stats(history: list[SalesRecord], window_days: int = 30) -> tuple[float, float]:
    """Return (mean, stddev) of daily sales in the window."""
    if not history:
        return 0.0, 0.0
    cutoff = max(r.date for r in history) - timedelta(days=window_days)

    # Bucket by date (sum multiple records for the same day)
    by_day: dict[date, int] = defaultdict(int)
    for rec in history:
        if rec.date >= cutoff:
            by_day[rec.date] += rec.units_sold

    if not by_day:
        return 0.0, 0.0

    # Fill missing days with 0 for accurate statistics
    min_d = min(by_day.keys())
    max_d = max(by_day.keys())
    series: list[float] = []
    cursor = min_d
    while cursor <= max_d:
        series.append(float(by_day.get(cursor, 0)))
        cursor += timedelta(days=1)

    if len(series) < 2:
        return series[0] if series else 0.0, 0.0
    return mean(series), stdev(series)


def generate_baseline_forecast(
    sku: str,
    history: list[SalesRecord],
    horizon_days: int = 90,
    start_date: Optional[date] = None,
    product_line: str = "工艺品",
) -> list[ForecastRecord]:
    """Generate a baseline forecast for one SKU.

    Returns one ForecastRecord per day in the forecast horizon.
    """
    if not history:
        # No history → zero forecast
        start = start_date or date.today()
        return [
            ForecastRecord(sku=sku, date=start + timedelta(days=i), forecast_units=0, source="baseline_empty")
            for i in range(horizon_days)
        ]

    base_rate = trailing_average(history, window_days=30)
    weekday_weights = compute_weekday_weights(history)
    trend_slope = compute_trend_slope(history, window_days=30)

    # Cap trend influence to prevent runaway
    max_trend_days = 60
    max_trend_delta = base_rate * 0.5  # trend can at most shift by 50% of base

    start = start_date or (max(r.date for r in history) + timedelta(days=1))

    records: list[ForecastRecord] = []
    for i in range(horizon_days):
        forecast_date = start + timedelta(days=i)

        # 1. Base rate
        raw = base_rate

        # 2. Apply trend (linear, capped)
        trend_days = min(i, max_trend_days)
        trend_delta = trend_slope * trend_days
        trend_delta = max(-max_trend_delta, min(max_trend_delta, trend_delta))
        raw += trend_delta

        # 3. Day of week weight
        raw *= weekday_weights[forecast_date.weekday()]

        # 4. Amazon peak multiplier (Prime Day / BFCM / Christmas)
        # These are the only peaks that affect demand for FBA cross-border sellers.
        peak_mult, _ = amazon_peak_multiplier(forecast_date)
        raw *= peak_mult

        units = max(0, round(raw))
        records.append(
            ForecastRecord(
                sku=sku,
                date=forecast_date,
                forecast_units=units,
                source="baseline",
            )
        )

    return records


def merge_with_manual_overrides(
    baseline: list[ForecastRecord],
    overrides: list[ForecastRecord],
) -> list[ForecastRecord]:
    """Merge baseline forecasts with manual overrides. Overrides win by (sku, date)."""
    override_map = {(r.sku, r.date): r for r in overrides}
    merged: list[ForecastRecord] = []
    for rec in baseline:
        key = (rec.sku, rec.date)
        if key in override_map:
            ov = override_map[key]
            merged.append(
                ForecastRecord(
                    sku=ov.sku,
                    date=ov.date,
                    forecast_units=ov.forecast_units,
                    source=ov.source or "manual",
                )
            )
        else:
            merged.append(rec)
    return merged
