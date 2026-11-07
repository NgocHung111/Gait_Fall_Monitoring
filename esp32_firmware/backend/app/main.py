import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
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
    wifi_data: WifiData | None = None
    step_timestamps_ms: list[int] | None = None
    step_intervals_ms: list[int] | None = None


class TelemetryResponse(BaseModel):
    received: bool
    event: str | None
    steps: int
    gait: GaitMetrics
    location: dict[str, float] | None
    location_error: str | None
    telegram_sent: bool


class TelemetryHistoryItem(TelemetryResponse):
    timestamp_utc: datetime


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
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
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)
telemetry_history: deque[TelemetryHistoryItem] = deque(maxlen=LOCAL_HISTORY_LIMIT)


def _read_telemetry_history(limit: int) -> list[TelemetryHistoryItem]:
    if not DATABASE_URL:
        return list(telemetry_history)[-limit:]

    import psycopg

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            rows = connection.execute(
                """
                SELECT record
                FROM telemetry_history
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
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


class TelemetryStorageError(RuntimeError):
    pass


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "storage": "postgres" if DATABASE_URL else "memory"}


@app.get("/api/telemetry/history", response_model=list[TelemetryHistoryItem])
def get_telemetry_history(limit: int = 250) -> list[TelemetryHistoryItem]:
    if limit < 1 or limit > 250:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 250.")
    try:
        return _read_telemetry_history(limit)
    except TelemetryStorageError as exc:
        logger.exception("Could not read telemetry history.")
        raise HTTPException(
            status_code=503, detail="Telemetry storage is temporarily unavailable."
        ) from exc


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
    location_error: str | None = None
    telegram_sent = False

    if is_fall:
        if not access_points:
            location_error = "No Wi-Fi access points were supplied."
        else:
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
                except GeolocationError as exc:
                    location_error = str(exc)

        try:
            send_emergency_message(
                bot_token=settings.telegram_bot_token.get_secret_value(),
                chat_id=settings.telegram_chat_id,
                latitude=location["lat"] if location else None,
                longitude=location["lng"] if location else None,
                steps=telemetry.steps,
            )
        except TelegramError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        telegram_sent = True

    response = TelemetryResponse(
        received=True,
        event=telemetry.event,
        steps=telemetry.steps,
        gait=gait,
        location=location,
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
