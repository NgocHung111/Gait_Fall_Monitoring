# Gait Monitoring API and Dashboard

## Run locally

```powershell
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal, serve the frontend:

```powershell
python -m http.server 5500 --directory ..\frontend
```

Open `http://localhost:5500`. The dashboard uses `http://localhost:8000` as its
local API and polls telemetry history every five seconds.

Set these environment variables before starting the backend when using the
corresponding integrations:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GOOGLE_GEOLOCATION_API_KEY`
- `DATABASE_URL` (PostgreSQL connection URL; without it, history is in memory)

`.env.example` lists the variable names only. Do not commit a real `.env` file
or paste credentials into source code.

## Free cloud deployment

The repository includes a Render Blueprint in `..\..\render.yaml`. It deploys
this FastAPI app and serves the dashboard from the same HTTPS origin. The
proposed service name is `gait-fall-monitoring`, giving the expected address
`https://gait-fall-monitoring.onrender.com`; Render assigns the final hostname
when the service is created, and it may differ if that name is unavailable.

1. Push the repository to GitHub without secrets, then create a free Neon
   PostgreSQL project. Copy its pooled or direct connection URL from Neon.
2. In Render, create a Blueprint from the GitHub repository and select the
   free plan. Set `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and
   `GOOGLE_GEOLOCATION_API_KEY` as Render environment variables. Leave optional
   integrations empty only if you do not need them.
3. Wait for the Render deploy to finish. Check
   `https://<your-render-host>/api/health`; it should report
   `"storage":"postgres"`. Open `https://<your-render-host>/` for the dashboard.
4. Put the exact HTTPS Render URL (without a trailing slash) in
   `esp32_firmware/include/config.h` as `SERVER_URL`, then rebuild and upload
   the firmware. The URL is public configuration, not a credential.
5. The ESP32-C3 requires a 2.4 GHz hotspot or Wi-Fi network. If it cannot
   connect to its saved network for 30 seconds, it opens a local setup
   network named `GaitWatch-Setup`, using the fixed password `GaitWatch2026!`.
   Connect a phone to that network, then open `http://192.168.4.1`. Select the
   nearby Wi-Fi, enter its password, and save; the ESP32 stores the credentials
   and reconnects without reflashing. Change `WIFI_SETUP_PASSWORD` in
   `esp32_firmware/include/config.h` if you want a different setup password.
   This password is shared by devices running this firmware, so anyone nearby
   who knows it can change the device's Wi-Fi settings.

Render's free web service spins down after 15 minutes without requests, so its
first request after idle can take about a minute. Free services have usage
limits and are intended for hobby/testing, not emergency-critical monitoring.
Neon's free plan provides persistent PostgreSQL storage, but has quotas and
scale-to-zero behavior; review current provider limits and export backups
regularly. The dashboard and history endpoint are publicly reachable at the
Render URL: do not send real health/location data until you have added suitable
access control for the people who should see it.

The current firmware posts telemetry when a fall alert is not canceled; it
does not yet upload continuous IMU samples or step timestamps. PostgreSQL
therefore preserves received alert records, not a full daily activity history.

The backend serves the frontend on the same origin in production, avoiding a
separate CORS/domain setup. Local CORS defaults permit the development frontend.

`POST /api/telemetry` accepts the ESP32 payload fields `event`, `steps`,
`uptime_ms`, and `wifi_data.wifiAccessPoints`. To calculate gait metrics, also
send either `step_timestamps_ms` or `step_intervals_ms`. Timestamps and intervals
are in milliseconds. If step timing is absent, gait metrics are returned as
`null`; aggregate step counts alone cannot determine cadence or variability.

`GET /api/telemetry/history?limit=250` returns up to 250 recent telemetry records
for the dashboard. When `DATABASE_URL` is configured, records are stored in
PostgreSQL and survive backend restarts. Without it, the local development
fallback keeps up to 250 records in memory and clears them on restart.

For `FALL_DETECTED`, the backend attempts Wi-Fi geolocation and sends a Telegram
alert. If location is unavailable, the alert is still sent and `location_error`
explains why the map link could not be included.
