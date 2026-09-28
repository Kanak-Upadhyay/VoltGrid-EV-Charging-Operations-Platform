from datetime import timedelta
from decimal import Decimal

from app.clock import utcnow
import app.db as database
from app.models import Charger
from tests.helpers import login

DRIVER = "driver@voltgrid.local"
OPERATOR = "operator@voltgrid.local"


def nearby(client, headers, radius=3):
    response = client.get(
        "/sites/nearby",
        params={"lat": 19.0659, "lng": 72.8687, "radius_km": radius},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def connector_on(site, serial, kind):
    for charger in site["chargers"]:
        if charger["serial_number"] == serial:
            for connector in charger["connectors"]:
                if connector["connector_type"] == kind:
                    return charger, connector
    raise AssertionError(f"{serial} {kind} missing")


def test_health_login_and_seeded_invoice(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    bad = client.post("/auth/login", json={"email": DRIVER, "password": "wrong-pass"})
    assert bad.status_code == 401
    headers = login(client, DRIVER)
    sessions = client.get("/sessions", headers=headers).json()
    completed = next(item for item in sessions if item["status"] == "completed")
    assert Decimal(completed["energy_kwh"]) == Decimal("22.000")
    assert Decimal(completed["invoice"]["total"]) == Decimal("677.79")
    assert Decimal(completed["invoice"]["idle_amount"]) == Decimal("0.00")


def test_nearby_search_is_limited_by_radius(client):
    headers = login(client, DRIVER)
    close = nearby(client, headers, 3)
    wide = nearby(client, headers, 10)
    assert [site["name"] for site in close] == ["BKC Superhub"]
    assert {site["name"] for site in wide} == {"BKC Superhub", "Andheri Metro Charge"}
    delhi = client.get(
        "/sites/nearby",
        params={"lat": 28.6315, "lng": 77.2167, "radius_km": 5},
        headers=headers,
    ).json()
    assert [site["name"] for site in delhi] == ["Connaught Place Hub"]


def test_metered_session_bills_from_snapshot_not_the_new_tariff(client):
    driver = login(client, DRIVER)
    operator = login(client, OPERATOR)
    site = nearby(client, driver, 3)[0]
    _, connector = connector_on(site, "VG-BS-01", "CCS2")
    opened = client.post("/sessions", json={"connector_id": connector["id"]}, headers=driver)
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    changed = client.post(
        f"/sites/{site['id']}/tariffs",
        json={
            "name": "Almost free",
            "energy_rate": "1.0000",
            "time_rate": "0",
            "idle_rate": "0",
            "grace_minutes": 10,
            "gst_percent": "18",
            "windows": [{"start_minute": 0, "end_minute": 1440, "multiplier": "1"}],
        },
        headers=operator,
    )
    assert changed.status_code == 201, changed.text
    reading = Decimal(connector["meter_kwh"]) + Decimal("5")
    meter = client.post(
        "/devices/meter",
        json={"connector_id": connector["id"], "cumulative_kwh": str(reading)},
        headers={"X-Device-Key": "device-VG-BS-01"},
    )
    assert meter.status_code == 200, meter.text
    closed = client.post(f"/sessions/{session_id}/stop", json={"simulate": False}, headers=driver)
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert Decimal(body["energy_kwh"]) == Decimal("5.000")
    assert body["estimated"] is False
    # 5 kWh on the original 18 INR tariff is at least 67.50 even in the cheapest window.
    assert Decimal(body["invoice"]["energy_amount"]) >= Decimal("67.50")


def test_duplicate_start_is_rejected_and_idempotency_key_replays(client):
    headers = login(client, DRIVER)
    site = nearby(client, headers, 3)[0]
    _, ccs = connector_on(site, "VG-BS-01", "CCS2")
    _, slow = connector_on(site, "VG-BS-01", "Type2")
    first = client.post("/sessions", json={"connector_id": ccs["id"]}, headers=headers)
    assert first.status_code == 201
    second = client.post("/sessions", json={"connector_id": ccs["id"]}, headers=headers)
    assert second.status_code == 409
    replay = client.post(
        "/sessions",
        json={"connector_id": slow["id"]},
        headers={**headers, "Idempotency-Key": "driver-click-1"},
    )
    again = client.post(
        "/sessions",
        json={"connector_id": slow["id"]},
        headers={**headers, "Idempotency-Key": "driver-click-1"},
    )
    assert replay.status_code == 201
    assert again.status_code == 201
    assert replay.json()["id"] == again.json()["id"]


def test_simulated_stop_can_include_idle_time_and_payment(client):
    headers = login(client, DRIVER)
    site = nearby(client, headers, 3)[0]
    _, connector = connector_on(site, "VG-BKC-02", "CCS2")
    opened = client.post("/sessions", json={"connector_id": connector["id"]}, headers=headers)
    assert opened.status_code == 201, opened.text
    unplugged = (utcnow() + timedelta(minutes=20)).isoformat()
    closed = client.post(
        f"/sessions/{opened.json()['id']}/stop",
        json={"simulate": True, "duration_minutes": 40, "unplugged_at": unplugged},
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["estimated"] is True
    assert Decimal(body["energy_kwh"]) > 0
    assert Decimal(body["invoice"]["idle_amount"]) == Decimal("20.00")
    paid = client.post(
        f"/invoices/{body['invoice']['id']}/pay",
        json={"method": "upi", "reference": "UPI123456"},
        headers=headers,
    )
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"
    again = client.post(
        f"/invoices/{body['invoice']['id']}/pay",
        json={"method": "upi", "reference": "UPI123456"},
        headers=headers,
    )
    assert again.json()["status"] == "paid"


def test_driver_cannot_read_analytics_or_create_sites(client):
    driver = login(client, DRIVER)
    operator = login(client, OPERATOR)
    assert client.get("/analytics/overview", headers=driver).status_code == 403
    denied = client.post(
        "/sites",
        json={"name": "Nope", "city": "Pune", "address": "FC Road", "latitude": "18.52", "longitude": "73.85"},
        headers=driver,
    )
    assert denied.status_code == 403
    created = client.post(
        "/sites",
        json={"name": "Koregaon Park", "city": "Pune", "address": "Lane 7", "latitude": "18.5362", "longitude": "73.8930"},
        headers=operator,
    )
    assert created.status_code == 201, created.text
    overview = client.get("/analytics/overview", headers=operator)
    assert overview.status_code == 200
    assert Decimal(overview.json()["revenue_inr"]) == Decimal("677.79")
    export = client.get("/analytics/revenue.csv", headers=operator)
    assert export.status_code == 200
    assert "invoice_id" in export.text
    assert "677.79" in export.text


def test_stale_heartbeat_takes_the_charger_offline(client):
    driver = login(client, DRIVER)
    operator = login(client, OPERATOR)
    wide = nearby(client, driver, 10)
    andheri = next(site for site in wide if site["name"] == "Andheri Metro Charge")
    charger_id = andheri["chargers"][0]["id"]
    connector_id = andheri["chargers"][0]["connectors"][0]["id"]
    db = database.SessionLocal()
    try:
        charger = db.get(Charger, charger_id)
        charger.last_heartbeat_at = utcnow() - timedelta(minutes=30)
        db.commit()
    finally:
        db.close()
    reconcile = client.post("/ops/reconcile", headers=operator)
    assert reconcile.status_code == 200
    assert reconcile.json()["marked_offline"] >= 1
    blocked = client.post("/sessions", json={"connector_id": connector_id}, headers=driver)
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "charger_offline"


def test_meter_rejects_a_reading_that_goes_backwards(client):
    headers = login(client, DRIVER)
    site = nearby(client, headers, 3)[0]
    _, connector = connector_on(site, "VG-BS-01", "Type2")
    response = client.post(
        "/devices/meter",
        json={"connector_id": connector["id"], "cumulative_kwh": "1"},
        headers={"X-Device-Key": "device-VG-BS-01"},
    )
    assert response.status_code == 409
    wrong_key = client.post(
        "/devices/meter",
        json={"connector_id": connector["id"], "cumulative_kwh": "500"},
        headers={"X-Device-Key": "nope"},
    )
    assert wrong_key.status_code == 401


def test_register_creates_a_driver_and_rejects_duplicates(client):
    payload = {"email": "new.driver@voltgrid.local", "password": "DriverPass1", "full_name": "Kabir Shah"}
    created = client.post("/auth/register", json=payload)
    assert created.status_code == 201
    assert created.json()["role"] == "driver"
    duplicate = client.post("/auth/register", json=payload)
    assert duplicate.status_code == 409
