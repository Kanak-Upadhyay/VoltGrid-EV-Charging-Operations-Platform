from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.billing import compute_charge, parse_windows, tariff_from_snapshot
from app.clock import as_utc_naive, utcnow
from app.config import get_settings
from app.db import mysql_lock
from app.errors import DomainError
from app.geo import bounding_box, haversine_km, money_km
from app.models import (
    AuditLog,
    Charger,
    ChargingSession,
    Connector,
    Invoice,
    Payment,
    Site,
    Tariff,
    User,
    Vehicle,
)
from app.security import device_key_matches


def audit(db: Session, actor_id: int | None, action: str, entity: str, entity_id: object, detail: dict | None = None) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            detail=detail or {},
            created_at=utcnow(),
        )
    )


def _snapshot(tariff: Tariff) -> dict:
    return {
        "tariff_id": tariff.id,
        "tariff_name": tariff.name,
        "energy_rate": str(tariff.energy_rate),
        "time_rate": str(tariff.time_rate),
        "idle_rate": str(tariff.idle_rate),
        "grace_minutes": tariff.grace_minutes,
        "gst_percent": str(tariff.gst_percent),
        "windows": tariff.windows,
    }


def _active_tariff(db: Session, site_id: int) -> Tariff:
    tariff = db.scalars(
        select(Tariff).where(Tariff.site_id == site_id, Tariff.active.is_(True)).order_by(Tariff.id.desc())
    ).first()
    if tariff is None:
        raise DomainError("no_tariff", "Site has no active tariff", 409)
    return tariff


def nearby_sites(
    db: Session,
    lat: float,
    lng: float,
    radius_km: float,
    connector_type: str | None = None,
    min_kw: Decimal | None = None,
) -> list[Site]:
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise DomainError("invalid_geo", "Latitude or longitude is out of range")
    if radius_km <= 0 or radius_km > 100:
        raise DomainError("invalid_geo", "Radius must be between 0 and 100 km")
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    sites = db.scalars(
        select(Site)
        .where(
            Site.latitude >= min_lat,
            Site.latitude <= max_lat,
            Site.longitude >= min_lng,
            Site.longitude <= max_lng,
        )
        .options(selectinload(Site.chargers).selectinload(Charger.connectors))
    ).all()
    ranked: list[tuple[float, Site]] = []
    for site in sites:
        distance = haversine_km(lat, lng, float(site.latitude), float(site.longitude))
        if distance > radius_km:
            continue
        if connector_type or min_kw is not None:
            matched = False
            for charger in site.chargers:
                for connector in charger.connectors:
                    if connector_type and connector.connector_type != connector_type:
                        continue
                    if min_kw is not None and connector.power_kw < min_kw:
                        continue
                    matched = True
            if not matched:
                continue
        site.distance_km = money_km(distance)
        ranked.append((distance, site))
    ranked.sort(key=lambda item: item[0])
    return [site for _, site in ranked]


def get_site(db: Session, site_id: int) -> Site:
    site = db.scalars(
        select(Site)
        .where(Site.id == site_id)
        .options(selectinload(Site.chargers).selectinload(Charger.connectors))
    ).first()
    if site is None:
        raise DomainError("not_found", "Site not found", 404)
    site.distance_km = None
    return site


def _require_network(user: User) -> int:
    if user.network_id is None:
        raise DomainError("forbidden", "User is not attached to a network", 403)
    return user.network_id


def create_site(db: Session, user: User, name: str, city: str, address: str, latitude: Decimal, longitude: Decimal) -> Site:
    network_id = _require_network(user)
    site = Site(
        network_id=network_id,
        name=name,
        city=city,
        address=address,
        latitude=latitude,
        longitude=longitude,
        created_at=utcnow(),
    )
    db.add(site)
    db.flush()
    audit(db, user.id, "site.create", "site", site.id, {"name": name})
    db.commit()
    db.refresh(site)
    site.chargers = []
    site.distance_km = None
    return site


def add_charger(db: Session, user: User, site_id: int, serial: str, vendor: str, max_kw: Decimal, device_key: str) -> Charger:
    from app.security import hash_device_key

    site = _owned_site(db, user, site_id)
    charger = Charger(
        site_id=site.id,
        serial_number=serial,
        vendor=vendor,
        max_kw=max_kw,
        status="online",
        device_key_hash=hash_device_key(device_key),
        last_heartbeat_at=utcnow(),
        created_at=utcnow(),
    )
    db.add(charger)
    db.flush()
    audit(db, user.id, "charger.create", "charger", charger.id, {"serial": serial})
    db.commit()
    db.refresh(charger)
    charger.connectors = []
    return charger


def add_connector(db: Session, user: User, charger_id: int, connector_type: str, power_kw: Decimal) -> Connector:
    charger = db.get(Charger, charger_id)
    if charger is None:
        raise DomainError("not_found", "Charger not found", 404)
    _owned_site(db, user, charger.site_id)
    if power_kw > charger.max_kw:
        raise DomainError("invalid_connector", "Connector power exceeds charger rating")
    connector = Connector(
        charger_id=charger.id,
        connector_type=connector_type,
        power_kw=power_kw,
        status="available",
        meter_kwh=Decimal("0"),
    )
    db.add(connector)
    db.flush()
    audit(db, user.id, "connector.create", "connector", connector.id, {"type": connector_type})
    db.commit()
    db.refresh(connector)
    return connector


def set_tariff(db: Session, user: User, site_id: int, payload: dict) -> Tariff:
    site = _owned_site(db, user, site_id)
    try:
        parse_windows(payload["windows"])
    except ValueError as exc:
        raise DomainError("invalid_tariff", str(exc)) from exc
    windows = [
        {
            "start_minute": int(item["start_minute"]),
            "end_minute": int(item["end_minute"]),
            "multiplier": str(item["multiplier"]),
        }
        for item in payload["windows"]
    ]
    for existing in db.scalars(select(Tariff).where(Tariff.site_id == site.id, Tariff.active.is_(True))):
        existing.active = False
    tariff = Tariff(
        site_id=site.id,
        name=payload["name"],
        energy_rate=Decimal(str(payload["energy_rate"])),
        time_rate=Decimal(str(payload["time_rate"])),
        idle_rate=Decimal(str(payload["idle_rate"])),
        grace_minutes=int(payload["grace_minutes"]),
        gst_percent=Decimal(str(payload["gst_percent"])),
        windows=windows,
        active=True,
        created_at=utcnow(),
    )
    db.add(tariff)
    db.flush()
    audit(db, user.id, "tariff.create", "tariff", tariff.id, {"site_id": site.id})
    db.commit()
    db.refresh(tariff)
    return tariff


def _owned_site(db: Session, user: User, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise DomainError("not_found", "Site not found", 404)
    if user.role != "admin" and site.network_id != user.network_id:
        raise DomainError("forbidden", "Site belongs to another network", 403)
    return site


def add_vehicle(db: Session, user: User, registration: str, battery_kwh: Decimal) -> Vehicle:
    vehicle = Vehicle(
        user_id=user.id,
        registration=registration.upper().replace(" ", ""),
        battery_kwh=battery_kwh,
        created_at=utcnow(),
    )
    db.add(vehicle)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DomainError("duplicate_vehicle", "You already saved this registration", 409) from exc
    db.refresh(vehicle)
    return vehicle


def list_vehicles(db: Session, user: User) -> list[Vehicle]:
    return list(db.scalars(select(Vehicle).where(Vehicle.user_id == user.id).order_by(Vehicle.id)))


def start_session(
    db: Session,
    user: User,
    connector_id: int,
    vehicle_id: int | None,
    idempotency_key: str | None,
) -> ChargingSession:
    if idempotency_key:
        existing = db.scalars(
            select(ChargingSession).where(
                ChargingSession.user_id == user.id,
                ChargingSession.idempotency_key == idempotency_key,
            )
        ).first()
        if existing is not None:
            return _load_session(db, existing.id)

    connector = db.scalars(mysql_lock(select(Connector).where(Connector.id == connector_id), db)).first()
    if connector is None:
        raise DomainError("not_found", "Connector not found", 404)
    charger = db.get(Charger, connector.charger_id)
    if charger is None or charger.status != "online":
        raise DomainError("charger_offline", "Charger is not accepting sessions", 409)
    if connector.status != "available":
        raise DomainError("unavailable", "Connector is not available", 409)
    if vehicle_id is not None:
        vehicle = db.scalars(
            select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user.id)
        ).first()
        if vehicle is None:
            raise DomainError("not_found", "Vehicle not found", 404)
    tariff = _active_tariff(db, charger.site_id)
    connector.status = "occupied"
    session = ChargingSession(
        user_id=user.id,
        connector_id=connector.id,
        site_id=charger.site_id,
        vehicle_id=vehicle_id,
        status="active",
        started_at=utcnow(),
        energy_kwh=Decimal("0"),
        meter_start=connector.meter_kwh,
        tariff_snapshot=_snapshot(tariff),
        idempotency_key=idempotency_key,
        estimated=False,
        created_at=utcnow(),
    )
    db.add(session)
    db.flush()
    audit(db, user.id, "session.start", "session", session.id, {"connector_id": connector.id})
    db.commit()
    return _load_session(db, session.id)


def post_meter(db: Session, device_key: str, connector_id: int, cumulative_kwh: Decimal) -> Connector:
    connector = db.scalars(mysql_lock(select(Connector).where(Connector.id == connector_id), db)).first()
    if connector is None:
        raise DomainError("not_found", "Connector not found", 404)
    charger = db.get(Charger, connector.charger_id)
    if charger is None or not device_key_matches(device_key, charger.device_key_hash):
        raise DomainError("unauthorized", "Device key rejected", 401)
    if cumulative_kwh < connector.meter_kwh:
        raise DomainError("meter_regression", "Meter reading moved backwards", 409)
    connector.meter_kwh = cumulative_kwh
    charger.last_heartbeat_at = utcnow()
    charger.status = "online"
    active = db.scalars(
        select(ChargingSession).where(
            ChargingSession.connector_id == connector.id,
            ChargingSession.status == "active",
        )
    ).first()
    if active is not None:
        active.energy_kwh = cumulative_kwh - active.meter_start
    audit(db, None, "meter.reading", "connector", connector.id, {"cumulative_kwh": str(cumulative_kwh)})
    db.commit()
    db.refresh(connector)
    return connector


def heartbeat(db: Session, device_key: str, charger_id: int) -> Charger:
    charger = db.get(Charger, charger_id)
    if charger is None or not device_key_matches(device_key, charger.device_key_hash):
        raise DomainError("unauthorized", "Device key rejected", 401)
    charger.last_heartbeat_at = utcnow()
    if charger.status == "offline":
        charger.status = "online"
    audit(db, None, "charger.heartbeat", "charger", charger.id, {})
    db.commit()
    db.refresh(charger)
    return charger


def _estimate_kwh(connector: Connector, started_at: datetime, ended_at: datetime) -> Decimal:
    hours = Decimal(int((ended_at - started_at).total_seconds())) / Decimal(3600)
    return (connector.power_kw * hours * Decimal("0.85")).quantize(Decimal("0.001"))


def stop_session(
    db: Session,
    user: User,
    session_id: int,
    unplugged_at: datetime | None,
    simulate: bool,
    duration_minutes: int | None = None,
) -> ChargingSession:
    session = db.scalars(mysql_lock(select(ChargingSession).where(ChargingSession.id == session_id), db)).first()
    if session is None:
        raise DomainError("not_found", "Session not found", 404)
    if user.role == "driver" and session.user_id != user.id:
        raise DomainError("forbidden", "This session belongs to another driver", 403)
    if session.status != "active":
        return _load_session(db, session.id)
    connector = db.scalars(mysql_lock(select(Connector).where(Connector.id == session.connector_id), db)).first()
    if connector is None:
        raise DomainError("not_found", "Connector not found", 404)
    ended_at = utcnow()
    if simulate and duration_minutes:
        if not get_settings().allow_meter_simulation:
            raise DomainError("simulation_disabled", "Meter simulation is disabled on this host", 403)
        age_seconds = (ended_at - session.started_at).total_seconds()
        if age_seconds > 300:
            raise DomainError(
                "simulation_window",
                "Simulated duration is only allowed during the first 5 minutes of a session",
                409,
            )
        session.started_at = ended_at - timedelta(minutes=duration_minutes)
    elif (ended_at - session.started_at).total_seconds() < 1:
        ended_at = session.started_at + timedelta(seconds=1)
    estimated = False
    delta = connector.meter_kwh - session.meter_start
    if delta == 0 and simulate:
        if not get_settings().allow_meter_simulation:
            raise DomainError("simulation_disabled", "Meter simulation is disabled on this host", 403)
        estimated_kwh = _estimate_kwh(connector, session.started_at, ended_at)
        connector.meter_kwh = session.meter_start + estimated_kwh
        delta = estimated_kwh
        estimated = True
    if delta < 0:
        raise DomainError("meter_regression", "Meter is below the session start reading", 409)
    if unplugged_at is not None:
        unplugged_at = as_utc_naive(unplugged_at)
    try:
        breakdown = compute_charge(
            energy_kwh=delta,
            started_at=session.started_at,
            ended_at=ended_at,
            unplugged_at=unplugged_at,
            tariff=tariff_from_snapshot(session.tariff_snapshot),
        )
    except ValueError as exc:
        raise DomainError("invalid_session", str(exc)) from exc
    session.status = "completed"
    session.ended_at = ended_at
    session.unplugged_at = unplugged_at
    session.energy_kwh = breakdown.energy_kwh
    session.meter_end = connector.meter_kwh
    session.estimated = estimated
    connector.status = "available"
    invoice = Invoice(
        session_id=session.id,
        energy_amount=breakdown.energy_amount,
        time_amount=breakdown.time_amount,
        idle_amount=breakdown.idle_amount,
        subtotal=breakdown.subtotal,
        gst_amount=breakdown.gst_amount,
        total=breakdown.total,
        currency="INR",
        status="issued",
        issued_at=ended_at,
    )
    db.add(invoice)
    audit(
        db,
        user.id,
        "session.stop",
        "session",
        session.id,
        {"total": str(breakdown.total), "estimated": estimated},
    )
    db.commit()
    return _load_session(db, session.id)


def _load_session(db: Session, session_id: int) -> ChargingSession:
    session = db.scalars(
        select(ChargingSession).where(ChargingSession.id == session_id).options(selectinload(ChargingSession.invoice))
    ).first()
    if session is None:
        raise DomainError("not_found", "Session not found", 404)
    return session


def list_sessions(db: Session, user: User, status: str | None, limit: int) -> list[ChargingSession]:
    stmt = select(ChargingSession).options(selectinload(ChargingSession.invoice)).order_by(ChargingSession.id.desc())
    if user.role == "driver":
        stmt = stmt.where(ChargingSession.user_id == user.id)
    elif user.role == "operator":
        site_ids = select(Site.id).where(Site.network_id == user.network_id)
        stmt = stmt.where(ChargingSession.site_id.in_(site_ids))
    if status:
        stmt = stmt.where(ChargingSession.status == status)
    return list(db.scalars(stmt.limit(min(limit, 200))))


def get_session(db: Session, user: User, session_id: int) -> ChargingSession:
    session = _load_session(db, session_id)
    if user.role == "driver" and session.user_id != user.id:
        raise DomainError("forbidden", "This session belongs to another driver", 403)
    if user.role == "operator":
        site = db.get(Site, session.site_id)
        if site is None or site.network_id != user.network_id:
            raise DomainError("forbidden", "Session is outside your network", 403)
    return session


def pay_invoice(db: Session, user: User, invoice_id: int, method: str, reference: str) -> Invoice:
    invoice = db.scalars(mysql_lock(select(Invoice).where(Invoice.id == invoice_id), db)).first()
    if invoice is None:
        raise DomainError("not_found", "Invoice not found", 404)
    session = db.get(ChargingSession, invoice.session_id)
    if session is None:
        raise DomainError("not_found", "Session not found", 404)
    if user.role == "driver" and session.user_id != user.id:
        raise DomainError("forbidden", "You cannot pay another driver's invoice", 403)
    if invoice.status == "paid":
        return invoice
    if invoice.status != "issued":
        raise DomainError("invoice_closed", "Invoice cannot be paid", 409)
    payment = Payment(
        invoice_id=invoice.id,
        amount=invoice.total,
        method=method,
        reference=reference,
        status="captured",
        paid_at=utcnow(),
    )
    invoice.status = "paid"
    db.add(payment)
    audit(db, user.id, "invoice.pay", "invoice", invoice.id, {"method": method, "reference": reference})
    db.commit()
    db.refresh(invoice)
    return invoice


def reconcile_stale_chargers(db: Session) -> int:
    settings = get_settings()
    cutoff = utcnow() - timedelta(minutes=settings.stale_heartbeat_minutes)
    stale = db.scalars(
        select(Charger).where(
            Charger.status == "online",
            Charger.last_heartbeat_at.is_not(None),
            Charger.last_heartbeat_at < cutoff,
        )
    ).all()
    for charger in stale:
        charger.status = "offline"
        audit(db, None, "charger.offline", "charger", charger.id, {"last_heartbeat_at": str(charger.last_heartbeat_at)})
    db.commit()
    return len(stale)


def analytics_overview(db: Session, user: User, site_id: int | None) -> dict:
    if user.role not in ("admin", "operator"):
        raise DomainError("forbidden", "Analytics are limited to operators", 403)
    session_stmt = select(ChargingSession).where(ChargingSession.status == "completed")
    if site_id is not None:
        _owned_site(db, user, site_id)
        session_stmt = session_stmt.where(ChargingSession.site_id == site_id)
    elif user.role == "operator":
        session_stmt = session_stmt.where(
            ChargingSession.site_id.in_(select(Site.id).where(Site.network_id == user.network_id))
        )
    sessions = list(db.scalars(session_stmt))
    energy = sum((s.energy_kwh for s in sessions), Decimal("0"))
    minutes = 0
    for item in sessions:
        if item.ended_at and item.started_at:
            minutes += int((item.ended_at - item.started_at).total_seconds() // 60)
    invoice_stmt = select(func.coalesce(func.sum(Invoice.total), 0)).join(
        ChargingSession, ChargingSession.id == Invoice.session_id
    ).where(Invoice.status.in_(("issued", "paid")))
    if site_id is not None:
        invoice_stmt = invoice_stmt.where(ChargingSession.site_id == site_id)
    elif user.role == "operator":
        invoice_stmt = invoice_stmt.where(
            ChargingSession.site_id.in_(select(Site.id).where(Site.network_id == user.network_id))
        )
    revenue = Decimal(str(db.scalar(invoice_stmt) or 0))
    connector_stmt = select(func.count(Connector.id)).join(Charger, Charger.id == Connector.charger_id).join(Site, Site.id == Charger.site_id)
    if site_id is not None:
        connector_stmt = connector_stmt.where(Site.id == site_id)
    elif user.role == "operator":
        connector_stmt = connector_stmt.where(Site.network_id == user.network_id)
    connector_count = int(db.scalar(connector_stmt) or 0)
    # Utilization against a 24h window so the number stays comparable across calls.
    capacity_minutes = connector_count * 24 * 60
    utilization = Decimal("0")
    if capacity_minutes:
        utilization = (Decimal(minutes) / Decimal(capacity_minutes) * Decimal(100)).quantize(Decimal("0.1"))
    return {
        "site_id": site_id,
        "sessions": len(sessions),
        "energy_kwh": energy,
        "revenue_inr": revenue,
        "charging_minutes": minutes,
        "utilization_pct": utilization,
    }


def revenue_rows(db: Session, user: User) -> list[dict]:
    if user.role not in ("admin", "operator"):
        raise DomainError("forbidden", "Analytics are limited to operators", 403)
    stmt = (
        select(Invoice, ChargingSession, Site)
        .join(ChargingSession, ChargingSession.id == Invoice.session_id)
        .join(Site, Site.id == ChargingSession.site_id)
        .order_by(Invoice.id.desc())
    )
    if user.role == "operator":
        stmt = stmt.where(Site.network_id == user.network_id)
    rows = []
    for invoice, session, site in db.execute(stmt):
        rows.append(
            {
                "invoice_id": invoice.id,
                "session_id": session.id,
                "site": site.name,
                "city": site.city,
                "energy_kwh": str(session.energy_kwh),
                "total_inr": str(invoice.total),
                "status": invoice.status,
                "issued_at": invoice.issued_at.isoformat(sep=" "),
            }
        )
    return rows


def list_audit(db: Session, limit: int) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 200))))

