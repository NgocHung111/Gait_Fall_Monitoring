import logging
import os
import re
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from app.core.auth import (
    create_access_token,
    decode_access_token,
    hash_device_code,
    hash_password,
    verify_password,
)
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
    device_id: str | None = Field(default=None, min_length=32, max_length=128)
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
    device_id: str | None = Field(default=None, exclude=True)


class RegisterRequest(BaseModel):
    username: str = Field(
        min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=32, max_length=128)


class AuthenticatedUser(BaseModel):
    id: int
    username: str
    device_id: str = Field(exclude=True)
    hashed_password: str | None = Field(default=None, exclude=True)


class PublicUser(BaseModel):
    id: int
    username: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
PHONE_LOCATION_MAX_AGE_SECONDS = 120
LOCAL_HISTORY_LIMIT = 250
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


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
                    device_id TEXT,
                    record JSONB NOT NULL
                )
                """
            )
            connection.execute(
                "ALTER TABLE telemetry_history ADD COLUMN IF NOT EXISTS device_id TEXT"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS telemetry_history_device_time_idx
                ON telemetry_history (device_id, timestamp_utc, id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    hashed_password TEXT NOT NULL,
                    device_id TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS phone_location_by_device (
                    device_id TEXT PRIMARY KEY REFERENCES users(device_id) ON DELETE CASCADE,
                    lat DOUBLE PRECISION NOT NULL,
                    lng DOUBLE PRECISION NOT NULL,
                    accuracy_m DOUBLE PRECISION,
                    timestamp_utc TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_control_by_device (
                    device_id TEXT PRIMARY KEY REFERENCES users(device_id) ON DELETE CASCADE,
                    reset_revision BIGINT NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_alert_contacts (
                    id BIGSERIAL PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES users(device_id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    UNIQUE (device_id, chat_id)
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
latest_phone_location: dict[str, PhoneLocationUpdate] = {}
step_reset_revisions: dict[str, int] = {}
alert_contacts: list[AlertContact] = []
alert_contacts_by_device: dict[str, list[AlertContact]] = {}
next_alert_contact_id = 1
alert_contacts_lock = threading.Lock()


class TelemetryStorageError(RuntimeError):
    pass


def _require_auth_storage() -> None:
    if not DATABASE_URL:
        raise HTTPException(
            status_code=503,
            detail="Account authentication requires persistent PostgreSQL storage.",
        )
    if len(JWT_SECRET_KEY) < 32:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET_KEY must contain at least 32 characters.",
        )


def _authenticated_user_from_row(row: tuple[object, ...] | None) -> AuthenticatedUser | None:
    if row is None:
        return None
    return AuthenticatedUser(id=row[0], username=row[1], device_id=row[2])


def _read_user_by_username(username: str) -> AuthenticatedUser | None:
    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                "SELECT id, username, device_id, hashed_password FROM users WHERE username = %s",
                (username,),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read the account.") from exc
    if row is None:
        return None
    return AuthenticatedUser(
        id=row[0], username=row[1], device_id=row[2], hashed_password=row[3]
    )


def _read_user_by_id(user_id: int) -> AuthenticatedUser | None:
    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                "SELECT id, username, device_id FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read the account.") from exc
    return _authenticated_user_from_row(row)


def _read_user_by_device_id(device_id: str) -> AuthenticatedUser | None:
    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                "SELECT id, username, device_id FROM users WHERE device_id = %s",
                (device_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read the device owner.") from exc
    return _authenticated_user_from_row(row)


def _create_user(
    username: str, hashed_password: str, device_id: str
) -> AuthenticatedUser:
    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                INSERT INTO users (username, hashed_password, device_id)
                VALUES (%s, %s, %s)
                RETURNING id, username, device_id
                """,
                (username, hashed_password, device_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=409,
            detail="Username or device code is already registered.",
        ) from exc
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not create the account.") from exc
    user = _authenticated_user_from_row(row)
    if user is None:
        raise TelemetryStorageError("Account creation returned no user.")
    return user


def _get_authenticated_user(
    token: str | None = Depends(oauth2_scheme),
) -> AuthenticatedUser:
    _require_auth_storage()
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    subject = decode_access_token(token, JWT_SECRET_KEY)
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = _read_user_by_id(int(subject))
    except TelemetryStorageError as exc:
        logger.exception("Could not validate the access token.")
        raise HTTPException(
            status_code=503, detail="Account storage is temporarily unavailable."
        ) from exc
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _get_device_owner(device_code: str | None) -> AuthenticatedUser:
    _require_auth_storage()
    if device_code is None:
        raise HTTPException(status_code=401, detail="Device pairing code is required.")
    try:
        device_id = hash_device_code(device_code)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid device pairing code.") from exc
    try:
        user = _read_user_by_device_id(device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not validate the device pairing code.")
        raise HTTPException(
            status_code=503, detail="Account storage is temporarily unavailable."
        ) from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Unregistered device.")
    return user


def _read_alert_contacts(device_id: str) -> list[AlertContact]:
    if not DATABASE_URL:
        with alert_contacts_lock:
            return list(alert_contacts_by_device.get(device_id, []))

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            rows = connection.execute(
                """
                SELECT id, name, chat_id, active
                FROM device_alert_contacts
                WHERE device_id = %s
                ORDER BY id
                """,
                (device_id,),
            ).fetchall()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read alert contacts.") from exc
    return [
        AlertContact(id=row[0], name=row[1], chat_id=row[2], active=row[3])
        for row in rows
    ]


def _create_alert_contact(
    contact: AlertContactCreate, device_id: str
) -> AlertContact:
    global next_alert_contact_id
    normalized_name = contact.name.strip()
    normalized_chat_id = contact.chat_id.strip()
    if not normalized_name:
        raise HTTPException(status_code=422, detail="Contact name cannot be blank.")
    if not re.fullmatch(r"-?\d{1,32}", normalized_chat_id):
        raise HTTPException(status_code=422, detail="Chat ID must be numeric.")

    if not DATABASE_URL:
        with alert_contacts_lock:
            contacts = alert_contacts_by_device.setdefault(device_id, [])
            if any(item.chat_id == normalized_chat_id for item in contacts):
                raise HTTPException(
                    status_code=409, detail="Chat ID already exists for this device."
                )
            created = AlertContact(
                id=next_alert_contact_id,
                name=normalized_name,
                chat_id=normalized_chat_id,
                active=True,
            )
            next_alert_contact_id += 1
            contacts.append(created)
            return created

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                INSERT INTO device_alert_contacts (device_id, name, chat_id, active)
                VALUES (%s, %s, %s, TRUE)
                RETURNING id, name, chat_id, active
                """,
                (device_id, normalized_name, normalized_chat_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=409, detail="Chat ID already exists for this device."
        ) from exc
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not create alert contact.") from exc
    return AlertContact(id=row[0], name=row[1], chat_id=row[2], active=row[3])


def _set_alert_contact_active(
    contact_id: int, active: bool, device_id: str
) -> AlertContact:
    if not DATABASE_URL:
        with alert_contacts_lock:
            contacts = alert_contacts_by_device.get(device_id, [])
            for index, contact in enumerate(contacts):
                if contact.id == contact_id:
                    updated = contact.model_copy(update={"active": active})
                    contacts[index] = updated
                    return updated
        raise HTTPException(status_code=404, detail="Alert contact not found.")

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                UPDATE device_alert_contacts
                SET active = %s
                WHERE id = %s AND device_id = %s
                RETURNING id, name, chat_id, active
                """,
                (active, contact_id, device_id),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not update alert contact.") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Alert contact not found.")
    return AlertContact(id=row[0], name=row[1], chat_id=row[2], active=row[3])


def _delete_alert_contact(contact_id: int, device_id: str) -> None:
    if not DATABASE_URL:
        with alert_contacts_lock:
            contacts = alert_contacts_by_device.get(device_id, [])
            for index, contact in enumerate(contacts):
                if contact.id == contact_id:
                    del contacts[index]
                    return
        raise HTTPException(status_code=404, detail="Alert contact not found.")

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            result = connection.execute(
                """
                DELETE FROM device_alert_contacts
                WHERE id = %s AND device_id = %s
                """,
                (contact_id, device_id),
            )
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not delete alert contact.") from exc
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alert contact not found.")


def _read_telemetry_history(
    limit: int | None, device_id: str, since: datetime | None = None
) -> list[TelemetryHistoryItem]:
    if not DATABASE_URL:
        records = [
            record for record in telemetry_history if record.device_id == device_id
        ]
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
                    WHERE device_id = %s AND timestamp_utc >= %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (device_id, since, limit),
                ).fetchall()
            elif since is not None:
                rows = connection.execute(
                    """
                    SELECT record
                    FROM telemetry_history
                    WHERE device_id = %s AND timestamp_utc >= %s
                    ORDER BY id
                    """,
                    (device_id, since),
                ).fetchall()
            elif limit is not None:
                rows = connection.execute(
                    """
                    SELECT record
                    FROM telemetry_history
                    WHERE device_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (device_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT record
                    FROM telemetry_history
                    WHERE device_id = %s
                    ORDER BY id
                    """,
                    (device_id,),
                ).fetchall()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read telemetry history.") from exc
    return [
        TelemetryHistoryItem.model_validate(row[0]).model_copy(
            update={"device_id": device_id}
        )
        for row in reversed(rows)
    ]

def _store_telemetry_record(record: TelemetryHistoryItem, device_id: str) -> None:
    if not DATABASE_URL:
        telemetry_history.append(record)
        return

    import psycopg
    from psycopg.types.json import Jsonb

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO telemetry_history (timestamp_utc, device_id, record)
                VALUES (%s, %s, %s)
                """,
                (
                    record.timestamp_utc,
                    device_id,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not persist telemetry record.") from exc


def _read_phone_location(device_id: str) -> PhoneLocationUpdate | None:
    if not DATABASE_URL:
        return latest_phone_location.get(device_id)

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                SELECT lat, lng, accuracy_m, timestamp_utc
                FROM phone_location_by_device
                WHERE device_id = %s
                """,
                (device_id,),
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


def _store_phone_location(location: PhoneLocationUpdate, device_id: str) -> None:
    if not DATABASE_URL:
        latest_phone_location[device_id] = location
        return

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO phone_location_by_device
                    (device_id, lat, lng, accuracy_m, timestamp_utc)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    lat = EXCLUDED.lat,
                    lng = EXCLUDED.lng,
                    accuracy_m = EXCLUDED.accuracy_m,
                    timestamp_utc = EXCLUDED.timestamp_utc
                """,
                (
                    device_id,
                    location.lat,
                    location.lng,
                    location.accuracy_m,
                    location.timestamp_utc,
                ),
            )
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not store the latest phone location.") from exc


def _read_step_reset_state(device_id: str) -> StepResetState:
    if not DATABASE_URL:
        return StepResetState(
            reset_revision=step_reset_revisions.get(device_id, 0)
        )

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                SELECT reset_revision FROM device_control_by_device
                WHERE device_id = %s
                """,
                (device_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not read the step reset state.") from exc
    return StepResetState(reset_revision=row[0] if row is not None else 0)


def _request_step_reset(device_id: str) -> StepResetState:
    if not DATABASE_URL:
        revision = step_reset_revisions.get(device_id, 0) + 1
        step_reset_revisions[device_id] = revision
        return StepResetState(reset_revision=revision)

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            row = connection.execute(
                """
                INSERT INTO device_control_by_device (device_id, reset_revision)
                VALUES (%s, 1)
                ON CONFLICT (device_id) DO UPDATE
                SET reset_revision = device_control_by_device.reset_revision + 1
                RETURNING reset_revision
                """
                ,
                (device_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise TelemetryStorageError("Could not request a step counter reset.") from exc
    if row is None:
        raise TelemetryStorageError("Could not increment the step reset state.")
    return StepResetState(reset_revision=row[0])


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "storage": "postgres" if DATABASE_URL else "memory"}


@app.post("/api/register", response_model=PublicUser, status_code=201)
def register_user(request: RegisterRequest) -> PublicUser:
    _require_auth_storage()
    username = request.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-32 letters, numbers, dots, underscores or hyphens.",
        )
    if len(request.password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=422, detail="Password must be at most 72 UTF-8 bytes."
        )
    try:
        device_id = hash_device_code(request.device_id.strip())
        hashed_password = hash_password(request.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        user = _create_user(username, hashed_password, device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not register account.")
        raise HTTPException(
            status_code=503, detail="Account storage is temporarily unavailable."
        ) from exc
    return PublicUser(id=user.id, username=user.username)


@app.post("/api/login", response_model=AccessTokenResponse)
def login_user(form: OAuth2PasswordRequestForm = Depends()) -> AccessTokenResponse:
    _require_auth_storage()
    try:
        user = _read_user_by_username(form.username.strip().lower())
    except TelemetryStorageError as exc:
        logger.exception("Could not sign in.")
        raise HTTPException(
            status_code=503, detail="Account storage is temporarily unavailable."
        ) from exc
    if (
        user is None
        or user.hashed_password is None
        or not verify_password(form.password, user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AccessTokenResponse(
        access_token=create_access_token(str(user.id), JWT_SECRET_KEY)
    )


@app.get("/api/me", response_model=PublicUser)
def get_current_account(
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> PublicUser:
    return PublicUser(id=current_user.id, username=current_user.username)


@app.get("/api/contacts", response_model=list[AlertContact])
def get_alert_contacts(
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> list[AlertContact]:
    try:
        return _read_alert_contacts(current_user.device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not read alert contacts.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.post("/api/contacts", response_model=AlertContact, status_code=201)
def add_alert_contact(
    contact: AlertContactCreate,
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> AlertContact:
    try:
        return _create_alert_contact(contact, current_user.device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not create alert contact.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.patch("/api/contacts/{contact_id}", response_model=AlertContact)
def update_alert_contact(
    contact_id: int,
    update: AlertContactStatus,
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> AlertContact:
    try:
        return _set_alert_contact_active(
            contact_id, update.active, current_user.device_id
        )
    except TelemetryStorageError as exc:
        logger.exception("Could not update alert contact.")
        raise HTTPException(
            status_code=503, detail="Alert contact storage is temporarily unavailable."
        ) from exc


@app.delete("/api/contacts/{contact_id}", status_code=204)
def remove_alert_contact(
    contact_id: int,
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> None:
    try:
        _delete_alert_contact(contact_id, current_user.device_id)
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
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> list[TelemetryHistoryItem]:
    if period is None and (limit < 1 or limit > 250):
        raise HTTPException(status_code=422, detail="limit must be between 1 and 250.")
    if since is not None and since.tzinfo is None:
        raise HTTPException(status_code=422, detail="since must include a timezone.")
    try:
        if period is not None:
            cutoff = since.astimezone(timezone.utc) if since is not None else None
            return _read_telemetry_history(None, current_user.device_id, cutoff)
        return _read_telemetry_history(limit, current_user.device_id, since)
    except TelemetryStorageError as exc:
        logger.exception("Could not read telemetry history.")
        raise HTTPException(
            status_code=503, detail="Telemetry storage is temporarily unavailable."
        ) from exc


@app.get("/api/device/reset-state", response_model=StepResetState)
def get_device_reset_state(
    device_code: str | None = Header(default=None, alias="X-Device-ID"),
) -> StepResetState:
    user = _get_device_owner(device_code)
    try:
        return _read_step_reset_state(user.device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not read the step reset state.")
        raise HTTPException(
            status_code=503, detail="Device command storage is temporarily unavailable."
        ) from exc


@app.post("/api/device/steps/reset", response_model=StepResetState)
def reset_device_steps(
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> StepResetState:
    try:
        return _request_step_reset(current_user.device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not request a step counter reset.")
        raise HTTPException(
            status_code=503, detail="Device command storage is temporarily unavailable."
        ) from exc


@app.post("/api/phone-location", response_model=PhoneLocationUpdate)
def update_phone_location(
    location: PhoneLocationUpdate,
    current_user: AuthenticatedUser = Depends(_get_authenticated_user),
) -> PhoneLocationUpdate:
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
        _store_phone_location(location, current_user.device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not store phone location.")
        raise HTTPException(
            status_code=503, detail="Phone location storage is temporarily unavailable."
        ) from exc
    return location


@app.post("/api/telemetry", response_model=TelemetryResponse)
def receive_telemetry(telemetry: Telemetry) -> TelemetryResponse:
    user = _get_device_owner(telemetry.device_id)
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
            phone_location = _read_phone_location(user.device_id)
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
                contact
                for contact in _read_alert_contacts(user.device_id)
                if contact.active
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
        device_id=user.device_id,
    )
    try:
        _store_telemetry_record(record, user.device_id)
    except TelemetryStorageError as exc:
        logger.exception("Could not persist telemetry record.")
        raise HTTPException(
            status_code=503, detail="Telemetry storage is temporarily unavailable."
        ) from exc
    return response


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
