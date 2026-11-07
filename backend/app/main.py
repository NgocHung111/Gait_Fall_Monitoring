from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.services.gait_analysis import GaitMetrics, analyze_gait
from backend.app.services.geolocation import GeolocationError, locate_wifi_access_points
from backend.app.services.telegram_bot import TelegramError, send_emergency_message


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
    telegram_sent: bool


app = FastAPI(title="Gait Fall Monitoring API")


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
    telegram_sent = False

    if is_fall and access_points:
        if not settings.google_geolocation_api_key.get_secret_value():
            raise HTTPException(
                status_code=503, detail="Google Geolocation API key is not configured."
            )
        try:
            location = locate_wifi_access_points(
                [access_point.model_dump(exclude_none=True) for access_point in access_points],
                settings.google_geolocation_api_key.get_secret_value(),
            )
        except GeolocationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if is_fall:
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

    return TelemetryResponse(
        received=True,
        event=telemetry.event,
        steps=telemetry.steps,
        gait=gait,
        location=location,
        telegram_sent=telegram_sent,
    )

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Gait and Fall Monitoring System is Running"}
