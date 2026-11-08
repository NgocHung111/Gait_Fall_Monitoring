# GaitWatch Dashboard

Serve this folder over HTTP (rather than opening `index.html` directly):

```powershell
python -m http.server 5500
```

Run the FastAPI backend separately, then open `http://localhost:5500`. The
dashboard polls `GET /api/telemetry/history` every five seconds. Its backend URL
can be changed in the dashboard and is saved in browser local storage.

The backend returns up to 250 recent telemetry records. The ESP32 sends a
`STEP_UPDATE` when its step count has changed, at most once every 10 seconds,
and sends a separate `FALL_DETECTED` record when a fall alert is not canceled.
It does not upload continuous IMU samples. Step updates include recent step
intervals (up to the latest 10), which the backend uses for cadence and gait
variability. Leaflet, OpenStreetMap tiles, Chart.js, and web fonts are loaded
from external CDNs and require an internet connection.
