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
local API and polls telemetry history every second.

Set these environment variables before starting the backend when using the
corresponding integrations:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GOOGLE_GEOLOCATION_API_KEY`
- `PHONE_LOCATION_TOKEN` (bearer token for phone GPS, step reset, and contact management)
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
   a randomly generated `PHONE_LOCATION_TOKEN` as Render environment variables.
   `GOOGLE_GEOLOCATION_API_KEY` is optional when using phone GPS instead of
   Wi-Fi geolocation.
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

### Free phone GPS for fall locations

The phone can share its built-in GPS without a Google billing account or an
additional GPS module. Generate a long random token (for example,
`python -c "import secrets; print(secrets.token_urlsafe(32))"`), set it as
`PHONE_LOCATION_TOKEN` in Render, and redeploy. On the dashboard opened on the
phone, enter that same token and select **Bật vị trí**. Allow location access.
The phone posts its coordinates every 15 seconds over HTTPS; the backend uses
the latest fix only if it is at most two minutes old when a fall arrives.
Recent phone GPS is preferred over Wi-Fi geolocation. If no fresh phone fix is
available, the backend can still use Google Wi-Fi geolocation when configured.

Keep the page open and the phone with the person wearing the device. Mobile
browsers may pause location updates when the page is backgrounded or closed,
and location is unavailable if the phone has no network connection. Stop
sharing when it is not needed. The phone-location token protects location
uploads, but this project does not yet authenticate dashboard/history access;
do not use it for sensitive real-world health monitoring without adding access
control. The map uses OpenStreetMap tiles with attribution and is subject to
the public tile service's usage policy.

### Activity distance, calories, and reset

The dashboard estimates distance as `steps × step length`. Estimated calories
use `distance_km × body_weight_kg × 0.5`; these are rough activity estimates,
not medical measurements. The default step length is 70 cm and default weight
is 65 kg. Edit them in the dashboard; values are saved only in that browser.

The **Reset số bước** button requires the same `PHONE_LOCATION_TOKEN` used for
phone location sharing. It creates a new counting session by issuing a
revisioned command; it does not delete telemetry history. The ESP32 polls for
the command while online and then resets its step counter and gait intervals.
The dashboard may take a few seconds to show zero. If the device is offline,
the command is applied after it reconnects.

### Telegram family alert contacts

The dashboard's **Danh sách người nhận cảnh báo Telegram** manager uses the
same `PHONE_LOCATION_TOKEN` entered in the dashboard's control-token field.
Contact list and mutation endpoints require that bearer token. Contacts are
stored in PostgreSQL when `DATABASE_URL` is configured; without PostgreSQL,
the local in-memory list is cleared when the backend restarts. On a confirmed
fall, Telegram alerts go to every active contact and the legacy
`TELEGRAM_CHAT_ID` recipient, with duplicate chat IDs sent only once. Use each
recipient's numeric Telegram Chat ID; the recipient must have started a
conversation with the bot so Telegram can deliver the alert.

The API provides `GET`/`POST /api/contacts`, `PATCH /api/contacts/{id}` to
change its `active` state, and `DELETE /api/contacts/{id}`. All require
`Authorization: Bearer <PHONE_LOCATION_TOKEN>`.

The firmware queues a step-count update as soon as it detects a new step, as
well as a separate record when a fall alert is not canceled. Network posting
runs in a separate task, and the firmware does not upload continuous IMU
samples. Step updates include recent step intervals (up to the latest 10) so
the backend can calculate cadence and gait variability.
PostgreSQL preserves these telemetry records, not a full daily activity history.

Telemetry records also include the ESP32 station SSID and RSSI in dBm. The
dashboard shows the latest reported network and signal strength. Battery telemetry is sampled on GPIO0 through
a 1:1 voltage divider (firmware multiplier 2.0), then linearly mapped from
3.2 V (0%) to 4.2 V (100%) and clamped to that range. The divider midpoint
must be wired to GPIO0 and share ground with the ESP32; never connect the LiPo
cell directly to the ADC pin. Battery badge colors indicate above 50% (green),
20%-50% (orange), and below 20% (red). The 1-hour and today chart filters use
the browser's local time zone; "All" queries the available stored history.

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
