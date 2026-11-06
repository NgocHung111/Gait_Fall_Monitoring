# Gait Monitoring Backend

Install dependencies and run the API from this directory:

```powershell
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Configure these environment variables before starting the server:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GOOGLE_GEOLOCATION_API_KEY`

`POST /api/telemetry` accepts the ESP32 payload fields `event`, `steps`,
`uptime_ms`, and `wifi_data.wifiAccessPoints`. To calculate gait metrics, also
send either `step_timestamps_ms` or `step_intervals_ms`. Timestamps and intervals
are in milliseconds. If step timing is absent, gait metrics are returned as
`null`; aggregate step counts alone cannot determine cadence or variability.

`GET /api/telemetry/history?limit=250` returns up to 250 recent telemetry records
for the dashboard. This history is held in memory and is cleared when the backend
restarts. The development frontend served from `http://localhost:5500` or
`http://127.0.0.1:5500` is allowed by CORS.

For `FALL_DETECTED`, the backend attempts Wi-Fi geolocation and sends a Telegram
alert. If location is unavailable, the alert is still sent and `location_error`
explains why the map link could not be included.
