"""Time-of-use charging invoice.

Energy is spread evenly across the session, then priced by the tariff
window each slice falls in. The charger meter owns the kWh total; this
module only prices it. Idle time is counted in started minutes after the
grace period, once the session has ended and the cable is still plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

PAISE = Decimal("0.01")
KWH = Decimal("0.0001")


@dataclass(frozen=True)
class Window:
    start_minute: int
    end_minute: int
    multiplier: Decimal


@dataclass(frozen=True)
class TariffView:
    energy_rate: Decimal
    time_rate: Decimal
    idle_rate: Decimal
    grace_minutes: int
    gst_percent: Decimal
    windows: tuple[Window, ...]


@dataclass(frozen=True)
class ChargeBreakdown:
    energy_kwh: Decimal
    charging_minutes: int
    idle_minutes: int
    energy_amount: Decimal
    time_amount: Decimal
    idle_amount: Decimal
    subtotal: Decimal
    gst_amount: Decimal
    total: Decimal


def q(value: Decimal, places: Decimal = PAISE) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def parse_windows(raw: list[dict]) -> tuple[Window, ...]:
    if not raw:
        raise ValueError("Tariff windows must cover 00:00-24:00")
    windows = []
    for item in raw:
        start = int(item["start_minute"])
        end = int(item["end_minute"])
        multiplier = Decimal(str(item["multiplier"]))
        if start < 0 or end > 1440 or end <= start or multiplier <= 0:
            raise ValueError("Invalid tariff window")
        windows.append(Window(start, end, multiplier))
    windows.sort(key=lambda w: w.start_minute)
    if windows[0].start_minute != 0 or windows[-1].end_minute != 1440:
        raise ValueError("Tariff windows must cover 00:00-24:00")
    for left, right in zip(windows, windows[1:]):
        if left.end_minute != right.start_minute:
            raise ValueError("Tariff windows must be contiguous")
    return tuple(windows)


def window_at(minute_of_day: int, windows: tuple[Window, ...]) -> Window:
    for window in windows:
        if window.start_minute <= minute_of_day < window.end_minute:
            return window
    raise ValueError("No tariff window covers this minute")


def _slices(started_at: datetime, ended_at: datetime, windows: tuple[Window, ...]) -> list[tuple[Window, int]]:
    total_seconds = int((ended_at - started_at).total_seconds())
    if total_seconds <= 0:
        raise ValueError("Session end must be after start")
    cursor = started_at
    slices: list[tuple[Window, int]] = []
    while cursor < ended_at:
        minute_of_day = cursor.hour * 60 + cursor.minute
        window = window_at(minute_of_day, windows)
        day_start = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = day_start + timedelta(minutes=window.end_minute)
        slice_end = min(ended_at, window_end)
        seconds = int((slice_end - cursor).total_seconds())
        if seconds <= 0:
            raise ValueError("Tariff window did not advance the clock")
        slices.append((window, seconds))
        cursor = slice_end
    return slices


def compute_charge(
    *,
    energy_kwh: Decimal,
    started_at: datetime,
    ended_at: datetime,
    unplugged_at: datetime | None,
    tariff: TariffView,
) -> ChargeBreakdown:
    if energy_kwh < 0:
        raise ValueError("Energy cannot be negative")
    if unplugged_at is not None and unplugged_at < ended_at:
        raise ValueError("Unplug time cannot be before session end")

    slices = _slices(started_at, ended_at, tariff.windows)
    total_seconds = sum(seconds for _, seconds in slices)
    remaining = q(energy_kwh, KWH)
    energy_amount = Decimal("0")
    allocated = Decimal("0")
    for index, (window, seconds) in enumerate(slices):
        if index == len(slices) - 1:
            slice_kwh = remaining - allocated
        else:
            slice_kwh = q(energy_kwh * Decimal(seconds) / Decimal(total_seconds), KWH)
            allocated += slice_kwh
        energy_amount += slice_kwh * tariff.energy_rate * window.multiplier

    charging_minutes = int(q(Decimal(total_seconds) / Decimal(60), Decimal("1")))
    time_amount = tariff.time_rate * Decimal(charging_minutes)

    idle_minutes = 0
    if unplugged_at is not None:
        plugged_seconds = int((unplugged_at - ended_at).total_seconds())
        plugged_minutes = (plugged_seconds + 59) // 60
        idle_minutes = max(0, plugged_minutes - tariff.grace_minutes)
    idle_amount = tariff.idle_rate * Decimal(idle_minutes)

    energy_amount = q(energy_amount)
    time_amount = q(time_amount)
    idle_amount = q(idle_amount)
    subtotal = q(energy_amount + time_amount + idle_amount)
    gst_amount = q(subtotal * tariff.gst_percent / Decimal(100))
    total = q(subtotal + gst_amount)
    return ChargeBreakdown(
        energy_kwh=q(energy_kwh, Decimal("0.001")),
        charging_minutes=charging_minutes,
        idle_minutes=idle_minutes,
        energy_amount=energy_amount,
        time_amount=time_amount,
        idle_amount=idle_amount,
        subtotal=subtotal,
        gst_amount=gst_amount,
        total=total,
    )


def tariff_from_snapshot(snapshot: dict) -> TariffView:
    return TariffView(
        energy_rate=Decimal(str(snapshot["energy_rate"])),
        time_rate=Decimal(str(snapshot["time_rate"])),
        idle_rate=Decimal(str(snapshot["idle_rate"])),
        grace_minutes=int(snapshot["grace_minutes"]),
        gst_percent=Decimal(str(snapshot["gst_percent"])),
        windows=parse_windows(snapshot["windows"]),
    )
