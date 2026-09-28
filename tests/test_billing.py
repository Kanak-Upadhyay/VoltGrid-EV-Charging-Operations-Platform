from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.billing import TariffView, compute_charge, parse_windows


def tariff() -> TariffView:
    return TariffView(
        energy_rate=Decimal("18"),
        time_rate=Decimal("0.50"),
        idle_rate=Decimal("2"),
        grace_minutes=10,
        gst_percent=Decimal("18"),
        windows=parse_windows(
            [
                {"start_minute": 0, "end_minute": 360, "multiplier": "0.75"},
                {"start_minute": 360, "end_minute": 1020, "multiplier": "1.00"},
                {"start_minute": 1020, "end_minute": 1440, "multiplier": "1.40"},
            ]
        ),
    )


def test_single_offpeak_window_prices_energy_time_and_gst():
    bill = compute_charge(
        energy_kwh=Decimal("10"),
        started_at=datetime(2026, 9, 1, 10, 0),
        ended_at=datetime(2026, 9, 1, 11, 0),
        unplugged_at=None,
        tariff=tariff(),
    )
    assert bill.energy_amount == Decimal("180.00")
    assert bill.time_amount == Decimal("30.00")
    assert bill.gst_amount == Decimal("37.80")
    assert bill.total == Decimal("247.80")


def test_session_split_across_peak_boundary():
    bill = compute_charge(
        energy_kwh=Decimal("12"),
        started_at=datetime(2026, 9, 1, 16, 50),
        ended_at=datetime(2026, 9, 1, 17, 10),
        unplugged_at=None,
        tariff=tariff(),
    )
    assert bill.energy_amount == Decimal("259.20")
    assert bill.time_amount == Decimal("10.00")
    assert bill.total == Decimal("317.66")


def test_session_crossing_midnight_uses_both_windows():
    bill = compute_charge(
        energy_kwh=Decimal("10"),
        started_at=datetime(2026, 9, 1, 23, 30),
        ended_at=datetime(2026, 9, 2, 0, 30),
        unplugged_at=None,
        tariff=tariff(),
    )
    assert bill.energy_amount == Decimal("193.50")
    assert bill.charging_minutes == 60
    assert bill.total == Decimal("263.73")


def test_idle_fee_starts_after_grace_period():
    bill = compute_charge(
        energy_kwh=Decimal("10"),
        started_at=datetime(2026, 9, 1, 10, 0),
        ended_at=datetime(2026, 9, 1, 11, 0),
        unplugged_at=datetime(2026, 9, 1, 11, 25),
        tariff=tariff(),
    )
    assert bill.idle_minutes == 15
    assert bill.idle_amount == Decimal("30.00")
    assert bill.total == Decimal("283.20")


def test_idle_inside_grace_is_free():
    started = datetime(2026, 9, 1, 18, 0)
    bill = compute_charge(
        energy_kwh=Decimal("22"),
        started_at=started,
        ended_at=started + timedelta(minutes=40),
        unplugged_at=started + timedelta(minutes=48),
        tariff=tariff(),
    )
    assert bill.idle_amount == Decimal("0.00")
    assert bill.total == Decimal("677.79")


def test_windows_must_cover_the_whole_day():
    with pytest.raises(ValueError):
        parse_windows([{"start_minute": 0, "end_minute": 600, "multiplier": "1"}])


def test_zero_length_session_is_rejected():
    moment = datetime(2026, 9, 1, 10, 0)
    with pytest.raises(ValueError):
        compute_charge(
            energy_kwh=Decimal("1"),
            started_at=moment,
            ended_at=moment,
            unplugged_at=None,
            tariff=tariff(),
        )
