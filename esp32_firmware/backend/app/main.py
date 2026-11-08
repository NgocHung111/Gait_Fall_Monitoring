import hmac
import logging
import os
import re
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from app.core.config import settings
from app.services.gait_analysis import GaitMetrics, analyze_gait
from app.services.geolocation import GeolocationError, locate_wifi_access_points
from app.services.telegram_bot import TelegramError, send_emergency_message


class WifiAccessPoint(BaseModel):
    macAddress: str = Field(pattern=r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    signalStrength: int
    channel: int | None = None


class WifiData(BaseModel):
    wifiAccessPoints: list[WifiAccessPoint] = Field(default_factory=list)


class Telemetry(BaseModel):
    event: str | None = None
    steps: int = Field(default=0, ge=0)
    uptime_ms: int | None = Field(default=None, ge=0)
    rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    wifi_ssid: str | None = Field(default=None, max_length=64)
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    wifi_data: WifiData | None = None
    step_timestamps_ms: list[int] | None = None
    step_intervals_ms: list[int] | None = None


class PhoneLocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    timestamp_utc: datetime


class StepResetState(BaseModel):
    reset_revision: int = Field(ge=0)


class AlertContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    chat_id: str = Field(min_length=1, max_length=32, pattern=r"^-?\d+$")


class AlertContact(BaseModel):
    id: int = Field(ge=1)
    name: str
    chat_id: str
    active: bool


class AlertContactStatus(BaseModel):
    active: bool


class TelemetryResponse(BaseModel):
    received: bool
    event: str | None
    steps: int
    gait: GaitMetrics
    location: dict[str, float] | None
    rssi_dbm: int | None = None
    wifi_ssid: str | None = None
    battery_percent: int | None = None
    location_source: str | None = None
    location_age_seconds: int | None = None
    location_error: str | None
    telegram_sent: bool


class TelemetryHistoryItem(TelemetryResponse):
    timestamp_utc: datetime


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PHONE_LOCATION_TOKEN = os.getenv("PHONE_LOCATION_TOKEN", "").strip()
PHONE_LOCATION_MAX_AGE_SECONDS = 120
LOCAL_HISTORY_LIMIT = 250
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if DATABASE_URL:
        import psycopg

        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_history (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp_utc TIMESTAMPTZ NOT NULL,
                    record JSONB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS phone_location (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    lat DOUBLE PRECISION NOT NULL,
                    lng DOUBLE PRECISION NOT NULL,
                    accuracy_m DOUBLE PRECISION,
                    timestamp_utc TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_control (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    reset_revision BIGINT NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                INSERT INTO device_control (id, reset_revision)
                VALUES (1, 0)
                ON CONFLICT (id) DO NOTHING
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_contacts (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    chat_id TEXT NOT NULL UNIQUE,
                    active BOOLEAN NOT NULL DEFAULT TRUE
                )
                """
            )
    else:
        logger.warning(
            "DATABASE_URL is not configured; telemetry history is in memory only."
        )
    yield


app = FastAPI(title="Gait Fall Monitoring API", lifespan=lifespan)
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)
telemetry_history: deque[TelemetryHistoryItem] = deque(maxlen=LOCAL_HISTORY_LIMIT)
latest_phone_location: PhoneLocationUpdate | None = None
step_reset_revision = 0
alert_contacts: list[AlertContact] = []
next_alert_contact_id = 1
alert_contacts_lock = threading.Lock()


def _read_alert_contacts() -> list[AlertContact]:
    if not DATABASE_URL:
        with alert_contacts_lock:
            return list(alert_contacts)

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            rows = connection.execute(
                """
                SELECT id, name, chat_id, active
                FROM alert_contacts
                ORDER BY id
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read alert contacts.") from exc
    return [
        AlertContact(id=row[0], name=row[1], chat_id=row[2], active=row[3])
        for row in rows
    ]


def _create_alert_contact(contact: AlertContactCreate) -> AlertContact:
    global next_alert_contact_id
    normalized_name = contact.name.strip()
    normalized_chat_id = contact.chat_id.strip()
    if not normalized_name:
        raise HTTPException(status_code=422, detail="Contact name cannot be blank.")
    if not re.fullmatch(r"-?\d{1,32}", normalized_chat_id):
        raise HTTPException(status_code=422, detail="Chat ID must be numeric.")

    if not DATABASE_URL:
        global alert_contacts
        with alert_contacts_lock:
            if any(item.chat_id == normalized_chat_id for item in alert_contacts):
                raise HTTPException(status_code=409, detail="Chat ID already exists.")
            created = AlertContact(
                id=next_alert_contact_id,
                name=normalized_name,
                chat_id=normalized_chat_id,
                active=True,
            )
            next_alert_contact_id += 1
            alert_contacts.append(created)
            return created

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                INSERT INTO alert_contacts (name, chat_id, active)
                VALUES (%s, %s, TRUE)
                RETURNING id, name, chat_id, active
                """,
                (normalized_name, normalized_chat_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Chat ID already exists.") from exc
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not create alert contact.") from exc
    return AlertContact(id=row[0], name=row[1], chat_id=row[2], active=row[3])


def _set_alert_contact_active(contact_id: int, active: bool) -> AlertContact:
    if not DATABASE_URL:
        with alert_contacts_lock:
            for index, contact in enumerate(alert_contacts):
                if contact.id == contact_id:
                    updated = contact.model_copy(update={"active": active})
                    alert_contacts[index] = updated
                    return updated
        raise HTTPException(status_code=404, detail="Alert contact not found.")

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                UPDATE alert_contacts
                SET active = %s
                WHERE id = %s
                RETURNING id, name, chat_id, active
                """,
                (active, contact_id),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not update alert contact.") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Alert contact not found.")
    return AlertContact(id=row[0], name=row[1], chat_id=row[2], active=row[3])


def _delete_alert_contact(contact_id: int) -> None:
    global alert_contacts
    if not DATABASE_URL:
        with alert_contacts_lock:
            for index, contact in enumerate(alert_contacts):
                if contact.id == contact_id:
                    del alert_contacts[index]
                    return
        raise HTTPException(status_code=404, detail="Alert contact not found.")

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            result = connection.execute(
                "DELETE FROM alert_contacts WHERE id = %s", (contact_id,)
            )
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not delete alert contact.") from exc
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alert contact not found.")


def _require_contact_management_token(authorization: str | None) -> None:
    if not PHONE_LOCATION_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Configure PHONE_LOCATION_TOKEN on the server to manage contacts.",
        )
    expected_authorization = f"Bearer {PHONE_LOCATION_TOKEN}"
    if authorization is None or not hmac.compare_digest(
        authorization.encode("utf-8"), expected_authorization.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid dashboard control token.")


def _read_telemetry_history(
    limit: int | None, since: datetime | None = None
) -> list[TelemetryHistoryItem]:
    if not DATABASE_URL:
        records = list(telemetry_history)
        if since is not None:
            records = [record for record in records if record.timestamp_utc >= since]
        return records if limit is None else records[-limit:]

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            if since is not None and limit is not None:
                rows = connection.execute(
                    """
                    SELECT record
                    FROM telemetry_history
                    WHERE timestamp_utc >= %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (since, limit),
                ).fetchall()
            elif since is not None:
                rows = connection.execute(
                    """
                    SELECT record
                    FROM telemetry_history
                    WHERE timestamp_utc >= %s
                    ORDER BY id
                    """,
                    (since,),
                ).fetchall()
            elif limit is not None:
                rows = connection.execute(
                    """
                    SELECT record
                    FROM telemetry_history
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT record FROM telemetry_history ORDER BY id"
                ).fetchall()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read telemetry history.") from exc
    return [TelemetryHistoryItem.model_validate(row[0]) for row in reversed(rows)]


def _store_telemetry_record(record: TelemetryHistoryItem) -> None:
    if not DATABASE_URL:
        telemetry_history.append(record)
        return

    import psycopg
    from psycopg.types.json import Jsonb

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO telemetry_history (timestamp_utc, record)
                VALUES (%s, %s)
                """,
                (record.timestamp_utc, Jsonb(record.model_dump(mode="json"))),
            )
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not persist telemetry record.") from exc


def _read_phone_location() -> PhoneLocationUpdate | None:
    if not DATABASE_URL:
        return latest_phone_location

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                SELECT lat, lng, accuracy_m, timestamp_utc
                FROM phone_location
                WHERE id = 1
                """
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read the latest phone location.") from exc
    if row is None:
        return None
    return PhoneLocationUpdate(
        lat=row[0],
        lng=row[1],
        accuracy_m=row[2],
        timestamp_utc=row[3],
    )


def _store_phone_location(location: PhoneLocationUpdate) -> None:
    global latest_phone_location
    if not DATABASE_URL:
        latest_phone_location = location
        return

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO phone_location (id, lat, lng, accuracy_m, timestamp_utc)
                VALUES (1, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    lat = EXCLUDED.lat,
                    lng = EXCLUDED.lng,
                    accuracy_m = EXCLUDED.accuracy_m,
                    timestamp_utc = EXCLUDED.timestamp_utc
                """,
                (
                    location.lat,
                    location.lng,
                    location.accuracy_m,
                    location.timestamp_utc,
                ),
            )
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not store the latest phone location.") from exc


class TelemetryStorageError(RuntimeError):
    pass


def _read_step_reset_state() -> StepResetState:
    if not DATABASE_URL:
        return StepResetState(reset_revision=step_reset_revision)

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                "SELECT reset_revision FROM device_control WHERE id = 1"
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read the step reset state.") from exc
    if row is None:
        raise TelemetryStorageError("The step reset state has not been initialized.")
    return StepResetState(reset_revision=row[0])


def _request_step_reset() -> StepResetState:
    global step_reset_revision
    if not DATABASE_URL:
        step_reset_revision += 1
        return StepResetState(reset_revision=step_reset_revision)

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                UPDATE device_control
                SET reset_revision = reset_revision + 1
                WHERE id = 1
                RETURNING reset_revision
                """
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not request a step counter reset.") from exc
    if row is None:
        raise TelemetryStorageError("The step reset state has not been initialized.")
    return StepResetState(reset_revision=row[0])


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "storage": "postgres" if DATABASE_URL else "memory"}


@app.get("/api/contacts", response_model=list[AlertContact])
def get_alert_contacts(
    authorization: str | None = Header(default=None),
) -> list[AlertContact]:
    _require_contact_management_token(authorization)
    try:
        return _read_alert_contacts()
    except TelemetryStorageError as exc:
        logger.exception("Could not read alert contacts.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.post("/api/contacts", response_model=AlertContact, status_code=201)
def add_alert_contact(
    contact: AlertContactCreate,
    authorization: str | None = Header(default=None),
) -> AlertContact:
    _require_contact_management_token(authorization)
    try:
        return _create_alert_contact(contact)
    except TelemetryStorageError as exc:
        logger.exception("Could not create alert contact.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.patch("/api/contacts/{contact_id}", response_model=AlertContact)
def update_alert_contact(
    contact_id: int,
    update: AlertContactStatus,
    authorization: str | None = Header(default=None),
) -> AlertContact:
    _require_contact_management_token(authorization)
    try:
        return _set_alert_contact_active(contact_id, update.active)
    except TelemetryStorageError as exc:
        logger.exception("Could not update alert contact.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.delete("/api/contacts/{contact_id}", status_code=204)
def remove_alert_contact(
    contact_id: int,
    authorization: str | None = Header(default=None),
) -> None:
    _require_contact_management_token(authorization)
    try:
        _delete_alert_contact(contact_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not delete alert contact.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.get("/api/telemetry/history", response_model=list[TelemetryHistoryItem])
def get_telemetry_history(
    limit: int = 250,
    since: datetime | None = None,
    period: Literal["hour", "today", "all"] | None = None,
) -> list[TelemetryHistoryItem]:
    if period is None and (limit < 1 or limit > 250):
        raise HTTPException(status_code=422, detail="limit must be between 1 and 250.")
    if since is not None and since.tzinfo is None:
        raise HTTPException(status_code=422, detail="since must include a timezone.")
    try:
        if period is not None:
            cutoff = since.astimezone(timezone.utc) if since is not None else None
            return _read_telemetry_history(None, cutoff)
        return _read_telemetry_history(limit, since)
    except TelemetryStorageError as exc:
        logger.exception("Could not read telemetry history.")
        raise HTTPException(
            status_code=503, detail="Telemetry storage is temporarily unavailable."
        ) from exc


@app.get("/api/device/reset-state", response_model=StepResetState)
def get_device_reset_state() -> StepResetState:
    try:
        return _read_step_reset_state()
    except TelemetryStorageError as exc:
        logger.exception("Could not read the step reset state.")
        raise HTTPException(
            status_code=503, detail="Device command storage is temporarily unavailable."
        ) from exc


@app.post("/api/device/steps/reset", response_model=StepResetState)
def reset_device_steps(
    authorization: str | None = Header(default=None),
) -> StepResetState:
    if not PHONE_LOCATION_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Configure PHONE_LOCATION_TOKEN on the server before resetting.",
        )
    expected_authorization = f"Bearer {PHONE_LOCATION_TOKEN}"
    if authorization is None or not hmac.compare_digest(
        authorization.encode("utf-8"), expected_authorization.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid dashboard control token.")
    try:
        return _request_step_reset()
    except TelemetryStorageError as exc:
        logger.exception("Could not request a step counter reset.")
        raise HTTPException(
            status_code=503, detail="Device command storage is temporarily unavailable."
        ) from exc


@app.post("/api/phone-location", response_model=PhoneLocationUpdate)
def update_phone_location(
    location: PhoneLocationUpdate,
    authorization: str | None = Header(default=None),
) -> PhoneLocationUpdate:
    if not PHONE_LOCATION_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Phone location sharing is not configured on the server.",
        )
    expected_authorization = f"Bearer {PHONE_LOCATION_TOKEN}"
    if authorization is None or not hmac.compare_digest(
        authorization.encode("utf-8"), expected_authorization.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid phone location token.")

    if location.timestamp_utc.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="timestamp_utc must include a timezone."
        )
    now = datetime.now(timezone.utc)
    age_seconds = (now - location.timestamp_utc.astimezone(timezone.utc)).total_seconds()
    if age_seconds > PHONE_LOCATION_MAX_AGE_SECONDS or age_seconds < -30:
        raise HTTPException(
            status_code=422, detail="Phone location timestamp is stale or in the future."
        )
    try:
        _store_phone_location(location)
    except TelemetryStorageError as exc:
        logger.exception("Could not store phone location.")
        raise HTTPException(
            status_code=503, detail="Phone location storage is temporarily unavailable."
        ) from exc
    return location


@app.post("/api/telemetry", response_model=TelemetryResponse)
def receive_telemetry(telemetry: Telemetry) -> TelemetryResponse:
    try:
        gait = analyze_gait(
            step_timestamps_ms=telemetry.step_timestamps_ms,
            step_intervals_ms=telemetry.step_intervals_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    is_fall = telemetry.event == "FALL_DETECTED"
    access_points = (
        telemetry.wifi_data.wifiAccessPoints if telemetry.wifi_data is not None else []
    )
    location: dict[str, float] | None = None
    location_source: str | None = None
    location_age_seconds: int | None = None
    location_error: str | None = None
    telegram_sent = False

    if is_fall:
        phone_location_error: str | None = None
        try:
            phone_location = _read_phone_location()
        except TelemetryStorageError:
            logger.exception("Could not read the latest phone location.")
            phone_location = None
            phone_location_error = "Phone location storage is temporarily unavailable."

        if phone_location is not None:
            location_age_seconds = max(
                0,
                int(
                    (
                        datetime.now(timezone.utc)
                        - phone_location.timestamp_utc.astimezone(timezone.utc)
                    ).total_seconds()
                ),
            )
            if location_age_seconds <= PHONE_LOCATION_MAX_AGE_SECONDS:
                location = {"lat": phone_location.lat, "lng": phone_location.lng}
                location_source = "phone"
            else:
                phone_location_error = (
                    f"Phone location is stale ({location_age_seconds} seconds old)."
                )

        if location is None and access_points:
            api_key = settings.google_geolocation_api_key.get_secret_value()
            if not api_key:
                location_error = "Google Geolocation API key is not configured."
            else:
                try:
                    location = locate_wifi_access_points(
                        [
                            access_point.model_dump(exclude_none=True)
                            for access_point in access_points
                        ],
                        api_key,
                    )
                    location_source = "wifi"
                    location_age_seconds = None
                except GeolocationError as exc:
                    location_error = str(exc)
        if location is None:
            if not access_points:
                location_error = phone_location_error or (
                    "No recent phone location or Wi-Fi access points were supplied."
                )
            elif phone_location_error and location_error:
                location_error = f"{phone_location_error} {location_error}"

        try:
            active_contacts = [
                contact for contact in _read_alert_contacts() if contact.active
            ]
        except TelemetryStorageError as exc:
            logger.exception("Could not load active alert contacts for a fall.")
            raise HTTPException(
                status_code=503,
                detail="Alert contact storage is temporarily unavailable.",
            ) from exc

        recipient_chat_ids = list(
            dict.fromkeys(
                chat_id
                for chat_id in [settings.telegram_chat_id]
                + [contact.chat_id for contact in active_contacts]
                if chat_id
            )
        )
        if not recipient_chat_ids:
            recipient_chat_ids = [settings.telegram_chat_id]

        delivery_failed = False
        for chat_id in recipient_chat_ids:
            try:
                send_emergency_message(
                    bot_token=settings.telegram_bot_token.get_secret_value(),
                    chat_id=chat_id,
                    latitude=location["lat"] if location else None,
                    longitude=location["lng"] if location else None,
                    steps=telemetry.steps,
                )
            except TelegramError:
                logger.exception("Telegram fall alert delivery failed.")
                delivery_failed = True
        if delivery_failed:
            raise HTTPException(
                status_code=502,
                detail="Telegram alert delivery failed for one or more recipients.",
            )
        telegram_sent = bool(recipient_chat_ids)

    response = TelemetryResponse(
        received=True,
        event=telemetry.event,
        steps=telemetry.steps,
        gait=gait,
        location=location,
        rssi_dbm=telemetry.rssi_dbm,
        wifi_ssid=telemetry.wifi_ssid,
        battery_percent=telemetry.battery_percent,
        location_source=location_source,
        location_age_seconds=location_age_seconds,
        location_error=location_error,
        telegram_sent=telegram_sent,
    )
    record = TelemetryHistoryItem(
        **response.model_dump(),
        timestamp_utc=datetime.now(timezone.utc),
    )
    try:
        _store_telemetry_record(record)
    except TelemetryStorageError as exc:
        logger.exception("Could not persist telemetry record.")
        raise HTTPException(
            status_code=503, detail="Telemetry storage is temporarily unavailable."
        ) from exc
    return response


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
