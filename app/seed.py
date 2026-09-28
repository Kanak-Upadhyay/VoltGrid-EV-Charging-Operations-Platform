"""Demo network used for local runs and the ops console.

Passwords and device keys are published on purpose. Do not point this
seed at a production database.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import compute_charge, tariff_from_snapshot
from app.clock import utcnow
from app.models import Charger, ChargingSession, Connector, Invoice, Network, Site, Tariff, User, Vehicle
from app.security import hash_device_key, hash_password
from app.services import _snapshot

DEMO_PASSWORD = "VoltGrid#2026"
ADMIN_EMAIL = "admin@voltgrid.local"
OPERATOR_EMAIL = "operator@voltgrid.local"
DRIVER_EMAIL = "driver@voltgrid.local"

WINDOWS = [
    {"start_minute": 0, "end_minute": 360, "multiplier": "0.75"},
    {"start_minute": 360, "end_minute": 1020, "multiplier": "1.00"},
    {"start_minute": 1020, "end_minute": 1440, "multiplier": "1.40"},
]

SITES = [
    ("BKC Superhub", "Mumbai", "G Block, Bandra Kurla Complex", "19.065900", "72.868700"),
    ("Andheri Metro Charge", "Mumbai", "Andheri East, Western Express Highway", "19.113600", "72.869700"),
    ("Connaught Place Hub", "Delhi", "Block A, Connaught Place", "28.631500", "77.216700"),
    ("Indiranagar DC", "Bengaluru", "100 Feet Road, Indiranagar", "12.978400", "77.640800"),
    ("Hitech City Hub", "Hyderabad", "Mindspace, Hitech City", "17.447400", "78.376200"),
]


def _tariff(site_id: int, name: str, now) -> Tariff:
    return Tariff(
        site_id=site_id,
        name=name,
        energy_rate=Decimal("18.0000"),
        time_rate=Decimal("0.5000"),
        idle_rate=Decimal("2.0000"),
        grace_minutes=10,
        gst_percent=Decimal("18.00"),
        windows=WINDOWS,
        active=True,
        created_at=now,
    )


def _charger(site_id: int, serial: str, max_kw: str, now) -> Charger:
    return Charger(
        site_id=site_id,
        serial_number=serial,
        vendor="VoltGrid Power",
        max_kw=Decimal(max_kw),
        status="online",
        device_key_hash=hash_device_key(f"device-{serial}"),
        last_heartbeat_at=now,
        created_at=now,
    )


def seed(db: Session) -> None:
    existing = db.scalars(select(User).where(User.email == ADMIN_EMAIL)).first()
    if existing is not None:
        return
    now = utcnow()
    network = Network(name="VoltGrid India", created_at=now)
    db.add(network)
    db.flush()

    db.add_all(
        [
            User(
                email=ADMIN_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                full_name="Asha Menon",
                role="admin",
                network_id=network.id,
                created_at=now,
            ),
            User(
                email=OPERATOR_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                full_name="Rohit Iyer",
                role="operator",
                network_id=network.id,
                created_at=now,
            ),
        ]
    )
    driver = User(
        email=DRIVER_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name="Neha Kapoor",
        role="driver",
        network_id=None,
        created_at=now,
    )
    db.add(driver)
    db.flush()
    db.add(
        Vehicle(
            user_id=driver.id,
            registration="MH01AB1234",
            battery_kwh=Decimal("60.00"),
            created_at=now,
        )
    )

    bkc_connector = None
    bkc_tariff = None
    for name, city, address, lat, lng in SITES:
        site = Site(
            network_id=network.id,
            name=name,
            city=city,
            address=address,
            latitude=Decimal(lat),
            longitude=Decimal(lng),
            created_at=now,
        )
        db.add(site)
        db.flush()
        tariff = _tariff(site.id, f"{city} TOU", now)
        db.add(tariff)
        code = "".join(word[0] for word in name.split())
        primary = _charger(site.id, f"VG-{code}-01", "60", now)
        db.add(primary)
        db.flush()
        ccs = Connector(
            charger_id=primary.id,
            connector_type="CCS2",
            power_kw=Decimal("60.00"),
            status="available",
            meter_kwh=Decimal("1000.000"),
        )
        type2 = Connector(
            charger_id=primary.id,
            connector_type="Type2",
            power_kw=Decimal("22.00"),
            status="available",
            meter_kwh=Decimal("400.000"),
        )
        db.add_all([ccs, type2])
        if name == "BKC Superhub":
            fast = _charger(site.id, "VG-BKC-02", "120", now)
            db.add(fast)
            db.flush()
            db.add(
                Connector(
                    charger_id=fast.id,
                    connector_type="CCS2",
                    power_kw=Decimal("120.00"),
                    status="available",
                    meter_kwh=Decimal("2500.000"),
                )
            )
            bkc_connector = ccs
            bkc_tariff = tariff
    db.flush()

    started = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    ended = started + timedelta(minutes=40)
    assert bkc_connector is not None and bkc_tariff is not None
    energy = Decimal("22.000")
    bkc_connector.meter_kwh = Decimal("1000.000") + energy
    snapshot = _snapshot(bkc_tariff)
    breakdown = compute_charge(
        energy_kwh=energy,
        started_at=started,
        ended_at=ended,
        unplugged_at=ended + timedelta(minutes=8),
        tariff=tariff_from_snapshot(snapshot),
    )
    session = ChargingSession(
        user_id=driver.id,
        connector_id=bkc_connector.id,
        site_id=bkc_tariff.site_id,
        vehicle_id=None,
        status="completed",
        started_at=started,
        ended_at=ended,
        unplugged_at=ended + timedelta(minutes=8),
        energy_kwh=breakdown.energy_kwh,
        meter_start=Decimal("1000.000"),
        meter_end=bkc_connector.meter_kwh,
        tariff_snapshot=snapshot,
        idempotency_key=None,
        estimated=False,
        created_at=ended,
    )
    db.add(session)
    db.flush()
    db.add(
        Invoice(
            session_id=session.id,
            energy_amount=breakdown.energy_amount,
            time_amount=breakdown.time_amount,
            idle_amount=breakdown.idle_amount,
            subtotal=breakdown.subtotal,
            gst_amount=breakdown.gst_amount,
            total=breakdown.total,
            currency="INR",
            status="issued",
            issued_at=ended,
        )
    )
    db.commit()
