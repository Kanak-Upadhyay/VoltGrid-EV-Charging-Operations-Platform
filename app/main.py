import logging
import threading
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Response
from sqlalchemy import text
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import __version__
from app.clock import utcnow
from app.config import apply_keyvault, get_settings
from app.db import get_db
from app.errors import DomainError
from app.export import revenue_csv, store_export
from app.models import User
from app.schemas import (
    AnalyticsOut,
    ChargerIn,
    ChargerOut,
    ConnectorIn,
    ConnectorOut,
    LoginIn,
    MeterIn,
    PayIn,
    RegisterIn,
    SessionOut,
    SiteIn,
    SiteOut,
    StartSessionIn,
    StopSessionIn,
    TariffIn,
    TokenOut,
    UserOut,
    VehicleIn,
    VehicleOut,
)
from app.security import create_token, get_current_user, hash_password, require_roles, user_by_email, verify_password
from app.services import (
    add_charger,
    add_connector,
    add_vehicle,
    analytics_overview,
    create_site,
    get_session,
    get_site,
    heartbeat,
    list_audit,
    list_sessions,
    list_vehicles,
    nearby_sites,
    pay_invoice,
    post_meter,
    reconcile_stale_chargers,
    revenue_rows,
    set_tariff,
    start_session,
    stop_session,
)

logger = logging.getLogger("voltgrid")
STATIC = Path(__file__).resolve().parent / "static"
_stop_reconciler = threading.Event()


def _reconcile_loop() -> None:
    settings = get_settings()
    while not _stop_reconciler.wait(settings.reconcile_interval_seconds):
        db = None
        try:
            from app.db import new_session

            db = new_session()
            marked = reconcile_stale_chargers(db)
            if marked:
                logger.info("marked %s chargers offline", marked)
        except Exception:
            logger.exception("reconciler failed")
            if db is not None:
                db.rollback()
        finally:
            if db is not None:
                db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if settings.appinsights_connection_string:
        logger.info("Application Insights connection string is configured")
    apply_keyvault(settings)
    from app.db import ensure_ready

    ensure_ready()
    thread = None
    if settings.run_reconciler:
        _stop_reconciler.clear()
        thread = threading.Thread(target=_reconcile_loop, name="voltgrid-reconciler", daemon=True)
        thread.start()
    yield
    _stop_reconciler.set()
    if thread is not None:
        thread.join(timeout=1)


app = FastAPI(
    title="VoltGrid",
    version=__version__,
    summary="EV charging network operations, metering, and GST billing",
    lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.exception_handler(DomainError)
def handle_domain(_: object, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "voltgrid", "time": utcnow().isoformat()}


@app.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post("/auth/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> User:
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise DomainError("invalid_email", "Email is not valid")
    if user_by_email(db, email) is not None:
        raise DomainError("duplicate_email", "An account with this email already exists", 409)
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name.strip(),
        role="driver",
        network_id=None,
        created_at=utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = user_by_email(db, body.email.strip().lower())
    if user is None or not verify_password(body.password, user.password_hash):
        raise DomainError("invalid_credentials", "Email or password is wrong", 401)
    return {
        "access_token": create_token(user),
        "role": user.role,
        "full_name": user.full_name,
    }


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@app.get("/sites/nearby", response_model=list[SiteOut])
def nearby(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(8),
    connector_type: str | None = Query(None),
    min_kw: Decimal | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list:
    return nearby_sites(db, lat, lng, radius_km, connector_type, min_kw)


@app.get("/sites/{site_id}", response_model=SiteOut)
def read_site(site_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> object:
    return get_site(db, site_id)


@app.post("/sites", response_model=SiteOut, status_code=201)
def post_site(
    body: SiteIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
) -> object:
    return create_site(db, user, body.name, body.city, body.address, body.latitude, body.longitude)


@app.post("/sites/{site_id}/chargers", response_model=ChargerOut, status_code=201)
def post_charger(
    site_id: int,
    body: ChargerIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
) -> object:
    try:
        return add_charger(db, user, site_id, body.serial_number, body.vendor, body.max_kw, body.device_key)
    except IntegrityError as exc:
        db.rollback()
        raise DomainError("duplicate_serial", "Charger serial already exists", 409) from exc


@app.post("/chargers/{charger_id}/connectors", response_model=ConnectorOut, status_code=201)
def post_connector(
    charger_id: int,
    body: ConnectorIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
) -> object:
    try:
        return add_connector(db, user, charger_id, body.connector_type, body.power_kw)
    except IntegrityError as exc:
        db.rollback()
        raise DomainError("duplicate_connector", "This connector type already exists on the charger", 409) from exc


@app.post("/sites/{site_id}/tariffs", status_code=201)
def post_tariff(
    site_id: int,
    body: TariffIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
) -> dict:
    tariff = set_tariff(db, user, site_id, body.model_dump(mode="json"))
    return {"id": tariff.id, "name": tariff.name, "active": tariff.active}


@app.post("/devices/meter", response_model=ConnectorOut)
def meter(
    body: MeterIn,
    db: Session = Depends(get_db),
    x_device_key: str = Header(default=""),
) -> object:
    return post_meter(db, x_device_key, body.connector_id, body.cumulative_kwh)


@app.post("/devices/heartbeat")
def device_heartbeat(
    charger_id: int = Query(...),
    db: Session = Depends(get_db),
    x_device_key: str = Header(default=""),
) -> dict:
    charger = heartbeat(db, x_device_key, charger_id)
    return {"id": charger.id, "status": charger.status, "last_heartbeat_at": charger.last_heartbeat_at}


@app.post("/vehicles", response_model=VehicleOut, status_code=201)
def post_vehicle(
    body: VehicleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> object:
    return add_vehicle(db, user, body.registration, body.battery_kwh)


@app.get("/vehicles", response_model=list[VehicleOut])
def get_vehicles(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list:
    return list_vehicles(db, user)


@app.post("/sessions", response_model=SessionOut, status_code=201)
def open_session(
    body: StartSessionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None),
) -> object:
    return start_session(db, user, body.connector_id, body.vehicle_id, idempotency_key)


@app.post("/sessions/{session_id}/stop", response_model=SessionOut)
def close_session(
    session_id: int,
    body: StopSessionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> object:
    return stop_session(db, user, session_id, body.unplugged_at, body.simulate, body.duration_minutes)


@app.get("/sessions", response_model=list[SessionOut])
def sessions(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list:
    return list_sessions(db, user, status, limit)


@app.get("/sessions/{session_id}", response_model=SessionOut)
def read_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> object:
    return get_session(db, user, session_id)


@app.post("/invoices/{invoice_id}/pay")
def pay(
    invoice_id: int,
    body: PayIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    invoice = pay_invoice(db, user, invoice_id, body.method, body.reference)
    return {"id": invoice.id, "status": invoice.status, "total": invoice.total}


@app.get("/analytics/overview", response_model=AnalyticsOut)
def overview(
    site_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
) -> dict:
    return analytics_overview(db, user, site_id)


@app.get("/analytics/revenue.csv")
def revenue_export(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
) -> Response:
    rows = revenue_rows(db, user)
    payload = revenue_csv(rows)
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    location = store_export(f"revenue-{stamp}.csv", payload)
    return Response(
        content=payload,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=revenue.csv",
            "X-Export-Location": location,
        },
    )


@app.post("/ops/reconcile")
def ops_reconcile(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "operator")),
) -> dict:
    return {"marked_offline": reconcile_stale_chargers(db)}


@app.get("/audit")
def audit_feed(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
) -> list[dict]:
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "entity": row.entity,
            "entity_id": row.entity_id,
            "detail": row.detail,
            "created_at": row.created_at,
        }
        for row in list_audit(db, limit)
    ]
