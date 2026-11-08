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
5. To use it away from home, copy
   `esp32_firmware/include/secrets.example.h` to the ignored local file
   `esp32_firmware/include/secrets.h` and put the phone hotspot SSID/password
   there. The ESP32-C3 requires 2.4 GHz hotspot support. Reflash if the hotspot
   credentials change.

Render's free web service spins down after 15 minutes without requests, so its
first request after idle can take about a minute. Free services have usage
limits and are intended for hobby/testing, not emergency-critical monitoring.
Neon's free plan provides persistent PostgreSQL storage, but has quotas and
scale-to-zero behavior; review current provider limits and export backups
regularly. The dashboard and history endpoint are publicly reachable at the
Render URL: do not send real health/location data until you have added suitable
access control for the people who should see it.

The firmware posts a step-count update every 10 seconds while the count has
changed, as well as a separate record when a fall alert is not canceled. It
does not upload continuous IMU samples. Step updates include recent step
intervals so the backend can calculate cadence and gait variability.
PostgreSQL preserves these telemetry records, not a full daily activity history.

The backend serves the frontend on the same origin in production, avoiding a
separate CORS/domain setup. Local CORS defaults permit the development frontend.

`POST /api/telemetry` accepts the ESP32 payload fields `event`, `steps`,
`uptime_ms`, and `wifi_data.wifiAccessPoints`. Step updates use the
`STEP_UPDATE` event and do not trigger Telegram alerts; `FALL_DETECTED` records
retain the fall notification and location behavior. To calculate gait metrics,
send either `step_timestamps_ms` or `step_intervals_ms`. Timestamps and
intervals are in milliseconds. If step timing is absent, gait metrics are
returned as `null`; aggregate step counts alone cannot determine cadence or
variability.

`GET /api/telemetry/history?limit=250` returns up to 250 recent telemetry records
for the dashboard. When `DATABASE_URL` is configured, records are stored in
PostgreSQL and survive backend restarts. Without it, the local development
fallback keeps up to 250 records in memory and clears them on restart.

For `FALL_DETECTED`, the backend attempts Wi-Fi geolocation and sends a Telegram
alert. If location is unavailable, the alert is still sent and `location_error`
explains why the map link could not be included.
