from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    sites: Mapped[list["Site"]] = relationship(back_populates="network")
    users: Mapped[list["User"]] = relationship(back_populates="network")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role in ('admin','operator','driver')", name="ck_user_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    network: Mapped[Network | None] = relationship(back_populates="users")
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="user")
    sessions: Mapped[list["ChargingSession"]] = relationship(back_populates="user")


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (Index("ix_sites_lat_lng", "latitude", "longitude"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80), index=True)
    address: Mapped[str] = mapped_column(String(255))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    network: Mapped[Network] = relationship(back_populates="sites")
    chargers: Mapped[list["Charger"]] = relationship(back_populates="site")
    tariffs: Mapped[list["Tariff"]] = relationship(back_populates="site")


class Charger(Base):
    __tablename__ = "chargers"
    __table_args__ = (CheckConstraint("status in ('online','offline','maintenance')", name="ck_charger_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    serial_number: Mapped[str] = mapped_column(String(64), unique=True)
    vendor: Mapped[str] = mapped_column(String(80))
    max_kw: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    status: Mapped[str] = mapped_column(String(20), default="online")
    device_key_hash: Mapped[str] = mapped_column(String(64))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    site: Mapped[Site] = relationship(back_populates="chargers")
    connectors: Mapped[list["Connector"]] = relationship(back_populates="charger")


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint("status in ('available','occupied','faulted')", name="ck_connector_status"),
        UniqueConstraint("charger_id", "connector_type", name="uq_connector_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    charger_id: Mapped[int] = mapped_column(ForeignKey("chargers.id"), index=True)
    connector_type: Mapped[str] = mapped_column(String(20))
    power_kw: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    meter_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"))

    charger: Mapped[Charger] = relationship(back_populates="connectors")
    sessions: Mapped[list["ChargingSession"]] = relationship(back_populates="connector")


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    energy_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    time_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    idle_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    grace_minutes: Mapped[int]
    gst_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    windows: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    site: Mapped[Site] = relationship(back_populates="tariffs")


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("user_id", "registration", name="uq_vehicle_reg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    registration: Mapped[str] = mapped_column(String(20))
    battery_kwh: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship(back_populates="vehicles")


class ChargingSession(Base):
    __tablename__ = "charging_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_session_idempotency"),
        CheckConstraint("status in ('active','completed','cancelled')", name="ck_session_status"),
        Index("ix_sessions_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    unplugged_at: Mapped[datetime | None] = mapped_column(DateTime)
    energy_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    meter_start: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    meter_end: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    tariff_snapshot: Mapped[dict] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship(back_populates="sessions")
    connector: Mapped[Connector] = relationship(back_populates="sessions")
    invoice: Mapped["Invoice | None"] = relationship(back_populates="session")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (CheckConstraint("status in ('issued','paid','void')", name="ck_invoice_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("charging_sessions.id"), unique=True)
    energy_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    time_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    idle_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="issued", index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime)

    session: Mapped[ChargingSession] = relationship(back_populates="invoice")
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="captured")
    paid_at: Mapped[datetime] = mapped_column(DateTime)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
