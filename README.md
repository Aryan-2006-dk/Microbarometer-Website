# Infrasonic Microbarometer — Web Dashboard

A web dashboard for a low-cost, capacitive-sensing microbarometer (AD7746-based),
built for Smart India Hackathon 2026. It can monitor a live sensor station in
real time, or analyze an uploaded CSV capture entirely offline — no server
round-trip needed for the CSV path.

## Features

- **Live sensor mode** — polls the backend every 5 s, showing the station's
  GPS location on a map, live humidity/temperature readouts, and a rolling
  24-hour graph.
- **Upload CSV mode** — load an AD7746 capture file (or this dashboard's own
  exported CSV) and get the same graphs, computed entirely in the browser.
- **6 selectable graphs**: Pressure, Capacitance, Humidity, Temperature vs
  time; FFT of pressure; Pressure vs Capacitance (the calibration curve).
- **5 plot styles**: line, area, scatter, step, points.
- **Editable calibration** (`C = slope · ΔP + offset`), saved on the server
  and applied retroactively — change it once and every graph, the raw-data
  table, and all exports update to match, including old data.
- **CSV / JSON export** of the full reading history (live mode only).

## Project structure

```
backend/
  app.py              Flask + SQLite API — stores readings & calibration,
                       serves the frontend
frontend/
  index.html           The dashboard itself (single page, vanilla JS)
  vendor/
    chart.umd.js        Self-hosted Chart.js build (no CDN dependency)
```

## Running it locally

```bash
cd backend
pip install flask
python app.py
```

Then open **http://localhost:5000** (or `http://<this machine's LAN IP>:5000`
from another device on the same network).

## How it works

- Live mode fetches `/api/readings?since=<24 hours ago>` every 5 seconds.
- Uploading a CSV is parsed entirely client-side — nothing is sent to the
  server in that mode.
- **Pressure is never stored directly.** It's calculated from capacitance
  using the calibration saved via `/api/calibration` every time data is
  read, so updating the calibration later corrects every graph, the table,
  and every export retroactively.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/reading` | `POST` | Push one reading: `{capacitance, humidity?, temperature?, lat?, lon?, timestamp?}` |
| `/api/readings?since=<ISO>` | `GET` | Readings from that time onward (used by the live graph) |
| `/api/readings?limit=<N>` | `GET` | Most recent N readings (used by CSV/JSON export) |
| `/api/calibration` | `GET` / `POST` | Read or update `{slope, offset, points}` |

## Hardware this pairs with

- **AD7746** 24-bit capacitance-to-digital converter (EVAL-AD7746EB), reading
  a Mylar diaphragm differential capacitive sensing cell
- **ESP32** for WiFi upload
- **DHT22** for humidity and temperature
- **NEO-6M GPS** module for station location

## Known limitation

Live FFT is limited to about 0.1 Hz by the current 5-second upload interval.
For full-resolution frequency analysis (e.g. a 50 Hz capture), use **Upload
CSV** mode instead.

---

Built for Smart India Hackathon 2026 — Team A.B.Y.S.S. (PS 26144, Smart Automation)
