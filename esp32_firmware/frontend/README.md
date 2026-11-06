# GaitWatch Dashboard

Serve this folder over HTTP (rather than opening `index.html` directly):

```powershell
python -m http.server 5500
```

Run the FastAPI backend separately, then open `http://localhost:5500`. The
dashboard polls `GET /api/telemetry/history` every five seconds. Its backend URL
can be changed in the dashboard and is saved in browser local storage.

The backend keeps only the last 250 telemetry records in memory. The current
ESP32 firmware sends telemetry when a fall is confirmed, so step charts contain
those received records rather than continuous samples. Cadence, step interval,
and variability appear when telemetry includes `step_timestamps_ms` or
`step_intervals_ms`. Leaflet, OpenStreetMap tiles, Chart.js, and web fonts are
loaded from external CDNs and require an internet connection.
