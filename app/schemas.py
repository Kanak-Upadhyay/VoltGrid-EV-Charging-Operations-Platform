from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str
    role: str
    network_id: int | None


class VehicleIn(BaseModel):
    registration: str = Field(min_length=4, max_length=20)
    battery_kwh: Decimal = Field(gt=0, le=300)


class VehicleOut(ORMModel):
    id: int
    registration: str
    battery_kwh: Decimal


class ConnectorOut(ORMModel):
    id: int
    connector_type: str
    power_kw: Decimal
    status: str
    meter_kwh: Decimal


class ChargerOut(ORMModel):
    id: int
    serial_number: str
    vendor: str
    max_kw: Decimal
    status: str
    last_heartbeat_at: datetime | None
    connectors: list[ConnectorOut]


class SiteOut(ORMModel):
    id: int
    name: str
    city: str
    address: str
    latitude: Decimal
    longitude: Decimal
    distance_km: Decimal | None = None
    chargers: list[ChargerOut]


class SiteIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=4, max_length=255)
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)


class ChargerIn(BaseModel):
    serial_number: str = Field(min_length=3, max_length=64)
    vendor: str = Field(min_length=2, max_length=80)
    max_kw: Decimal = Field(gt=0, le=500)
    device_key: str = Field(min_length=8, max_length=128)


class ConnectorIn(BaseModel):
    connector_type: str = Field(pattern="^(CCS2|Type2|CHAdeMO)$")
    power_kw: Decimal = Field(gt=0, le=500)


class WindowIn(BaseModel):
    start_minute: int = Field(ge=0, le=1440)
    end_minute: int = Field(ge=0, le=1440)
    multiplier: Decimal = Field(gt=0, le=10)


class TariffIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    energy_rate: Decimal = Field(ge=0, le=200)
    time_rate: Decimal = Field(ge=0, le=50)
    idle_rate: Decimal = Field(ge=0, le=50)
    grace_minutes: int = Field(ge=0, le=180)
    gst_percent: Decimal = Field(ge=0, le=40)
    windows: list[WindowIn]


class StartSessionIn(BaseModel):
    connector_id: int
    vehicle_id: int | None = None


class StopSessionIn(BaseModel):
    unplugged_at: datetime | None = None
    simulate: bool = False
    duration_minutes: int | None = Field(default=None, ge=1, le=240)


class MeterIn(BaseModel):
    connector_id: int
    cumulative_kwh: Decimal = Field(ge=0)


class PayIn(BaseModel):
    method: str = Field(pattern="^(upi|card|wallet)$")
    reference: str = Field(min_length=4, max_length=80)


class InvoiceOut(ORMModel):
    id: int
    energy_amount: Decimal
    time_amount: Decimal
    idle_amount: Decimal
    subtotal: Decimal
    gst_amount: Decimal
    total: Decimal
    currency: str
    status: str
    issued_at: datetime


class SessionOut(ORMModel):
    id: int
    user_id: int
    connector_id: int
    site_id: int
    vehicle_id: int | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    unplugged_at: datetime | None
    energy_kwh: Decimal
    estimated: bool
    invoice: InvoiceOut | None = None


class AnalyticsOut(BaseModel):
    site_id: int | None
    sessions: int
    energy_kwh: Decimal
    revenue_inr: Decimal
    charging_minutes: int
    utilization_pct: Decimal
