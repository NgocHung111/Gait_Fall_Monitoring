from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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


app = FastAPI(title="Gait Fall Monitoring API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept"],
)
telemetry_history: deque[TelemetryHistoryItem] = deque(maxlen=250)


@app.get("/api/telemetry/history", response_model=list[TelemetryHistoryItem])
def get_telemetry_history(limit: int = 250) -> list[TelemetryHistoryItem]:
    if limit < 1 or limit > 250:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 250.")
    return list(telemetry_history)[-limit:]


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
    telemetry_history.append(
        TelemetryHistoryItem(
            **response.model_dump(),
            timestamp_utc=datetime.now(timezone.utc),
        )
    )
    return response
