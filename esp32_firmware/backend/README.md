# Gait Monitoring API and Dashboard

## Run locally

```powershell
python -m pip install -r requirements.txt
$env:DATABASE_URL = "postgresql://..."
$env:JWT_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal, serve the frontend:

```powershell
python -m http.server 5500 --directory ..\frontend
```

Open `http://localhost:5500`. The dashboard uses `http://localhost:8000` as its
local API and polls telemetry history every second. PostgreSQL and a stable
`JWT_SECRET_KEY` of at least 32 characters are required for accounts. Optional
integrations use `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and
`GOOGLE_GEOLOCATION_API_KEY`. Never commit credentials or pairing codes.

## Free cloud deployment

The repository includes a Render Blueprint in `..\..\render.yaml`. It deploys
this FastAPI app and serves the dashboard from the same HTTPS origin. The
proposed service name is `gait-fall-monitoring`, giving the expected address
`https://gait-fall-monitoring.onrender.com`; Render assigns the final hostname
when the service is created.

1. Push the repository to GitHub without secrets, then create a free Neon
   PostgreSQL project and copy its pooled or direct connection URL.
2. In Render, create a Blueprint from the GitHub repository. Set `DATABASE_URL`,
   `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` as needed. The Blueprint
   generates a private `JWT_SECRET_KEY`.
3. Wait for deployment. Check `https://<your-render-host>/api/health`; it should
   report `"storage":"postgres"`. Open `https://<your-render-host>/`.
4. Put the exact HTTPS Render URL (without a trailing slash) in
   `esp32_firmware/include/config.h` as `SERVER_URL`, then rebuild and upload
   the firmware.
5. The ESP32-C3 requires a 2.4 GHz hotspot or Wi-Fi network. If it cannot
   connect to its saved network for 30 seconds, it opens a local setup network
   named `GaitWatch-Setup`, using the fixed password `GaitWatch2026!`. Connect a
   phone to that network, then open `http://192.168.4.1`; select the nearby
   Wi-Fi, enter its password, and save. Change `WIFI_SETUP_PASSWORD` in
   `esp32_firmware/include/config.h` to change the setup password.

Render's free web service spins down after 15 minutes without requests. Neon
free plans have quotas and scale-to-zero behavior; review provider limits and
export backups regularly. These services are intended for hobby/testing, not
emergency-critical monitoring.

### Accounts and device pairing

Generate a unique random pairing code of at least 32 URL-safe characters:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add it to the ignored local `esp32_firmware/include/secrets.h` as
`DEVICE_PAIRING_CODE`, then build and flash the firmware. Do not commit or share
this code. During dashboard registration, enter the same code; it is stored as
a SHA-256 hash and can only be claimed by one account. The ESP32 sends it with
telemetry over HTTPS. Telemetry and reset-state requests from unregistered
devices are rejected. Use a distinct code for each ESP32. Historical records
created before pairing have no owner and are intentionally not visible to new
accounts.

Register and sign in from the dashboard. Each account is bound to one device,
and dashboard data APIs return only that device's data. The browser stores its
access token in `localStorage`; signing out removes it.

### Phone GPS for fall locations

Sign in to the account paired with the ESP32 on the phone, select **Bật vị trí**,
and allow location access. The phone posts coordinates every 15 seconds over
HTTPS; a fix is used only if it is at most two minutes old when a fall arrives.
Recent phone GPS is preferred over Wi-Fi geolocation. If no fresh phone fix is
available, the backend can use Google Wi-Fi geolocation when configured.

Keep the page open and the phone with the person wearing the device. Mobile
browsers may pause location updates when the page is backgrounded or closed;
location is unavailable if the phone has no network connection. Stop sharing
when it is not needed. The map uses OpenStreetMap tiles with attribution and is
subject to the public tile service's usage policy.

### Activity and step reset

The dashboard estimates distance as `steps × step length`. Estimated calories
use `distance_km × body_weight_kg × 0.5`; these are rough activity estimates,
not medical measurements. The default step length is 70 cm and default weight
is 65 kg. Values are saved only in that browser.

The **Reset số bước** button uses the signed-in account. It creates a new
counting session by issuing a revisioned command; it does not delete telemetry
history. The ESP32 polls for the command while online and then resets its step
counter and gait intervals. If offline, the command is applied after reconnect.

### Telegram family alert contacts

The **Danh sách người nhận cảnh báo Telegram** manager uses the signed-in
account. Contacts are stored per paired device in PostgreSQL. On a confirmed
fall, Telegram alerts go to that device's active contacts and the legacy
`TELEGRAM_CHAT_ID` recipient, with duplicate chat IDs sent only once. Use each
recipient's numeric Telegram Chat ID; the recipient must have started a
conversation with the bot so Telegram can deliver the alert.

The API provides `GET`/`POST /api/contacts`, `PATCH /api/contacts/{id}` to
change its `active` state, and `DELETE /api/contacts/{id}`. Dashboard requests
require a valid JWT bearer token.

## Telemetry and API

The firmware queues a step-count update when it detects a new step, and a
separate record when a fall alert is not canceled. It does not upload
continuous IMU samples. Step updates include recent step intervals (up to 10)
for cadence and gait variability. PostgreSQL preserves telemetry records, not a
full daily activity history.

Telemetry includes the ESP32 station SSID and RSSI in dBm. The dashboard shows
the latest reported network and signal strength. Battery telemetry is sampled
on GPIO0 through a 1:1 voltage divider (firmware multiplier 2.0), then mapped
from 3.2 V (0%) to 4.2 V (100%) and clamped. Never connect the LiPo cell
directly to the ADC pin. Battery badge colors indicate above 50% (green),
20%-50% (orange), and below 20% (red). The 1-hour and today chart filters use
the browser's local time zone; "All" queries available stored history.

`POST /api/register` creates an account bound to an unclaimed device pairing
code. `POST /api/login` accepts OAuth2 password-form fields and returns a JWT
access token; `GET /api/me` returns the signed-in account. Dashboard APIs,
including `GET /api/telemetry/history?limit=250`, require that bearer token and
return data only for its paired device. `POST /api/telemetry` and the ESP32's
`GET /api/device/reset-state` authenticate with the pairing code. PostgreSQL is
required for accounts and durable data.

`POST /api/telemetry` accepts `event`, `steps`, `uptime_ms`, and
`wifi_data.wifiAccessPoints`. Step updates use `STEP_UPDATE` and do not trigger
Telegram alerts; `FALL_DETECTED` retains fall notification and location
behavior. To calculate gait metrics, send either `step_timestamps_ms` or
`step_intervals_ms`, in milliseconds. If timing is absent, gait metrics are
`null`; aggregate step counts alone cannot determine cadence or variability.
