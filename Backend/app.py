"""
Infrasonic microbarometer backend.

Receives capacitance / humidity / temperature / GPS readings pushed by the
ESP32 over WiFi, stores them in SQLite, and serves them to the dashboard.

Pressure is NOT stored: it is calculated from capacitance every time data is
read, using the calibration saved on the server (see /api/calibration). That
way, if you change the calibration later, every graph and every export
(including old data) updates to match.

Run:
    pip install flask
    python app.py

Then open http://localhost:5000 in a browser (or http://<this-pc's-LAN-IP>:5000
from another device on the same network).
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

# ---------------------------------------------------------------------
# Default calibration
# ---------------------------------------------------------------------
# From your experiment (0-196.2 Pa, R^2 ~ 0.9769):
#
#     C = DEFAULT_SLOPE * dP + DEFAULT_OFFSET      (C in pF, dP in Pa)
#
# so pressure is recovered as   dP = (C - offset) / slope.
# These are only the starting values - once you press "Apply" on the
# dashboard's calibration panel, the saved values in readings.db win.
DEFAULT_SLOPE = 0.02107    # pF per Pa
DEFAULT_OFFSET = 0.10864   # pF
DEFAULT_POINTS = [         # [pressure Pa, capacitance pF] calibration data
    [0.00, 0.20], [19.62, 0.27], [39.24, 0.65], [58.86, 1.66],
    [78.48, 1.73], [98.10, 2.40], [117.72, 2.80], [137.34, 2.82],
    [156.96, 3.59], [176.58, 3.716], [196.20, 4.096],
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "readings.db")
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                capacitance REAL NOT NULL,
                pressure REAL,
                humidity REAL,
                temperature REAL,
                lat REAL,
                lon REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                slope REAL NOT NULL,
                intercept REAL NOT NULL,
                points TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO calibration (id, slope, intercept, points) "
            "VALUES (1, ?, ?, ?)",
            (DEFAULT_SLOPE, DEFAULT_OFFSET, json.dumps(DEFAULT_POINTS)),
        )


def load_calibration():
    with get_db() as conn:
        row = conn.execute(
            "SELECT slope, intercept, points FROM calibration WHERE id = 1"
        ).fetchone()
    return {
        "slope": row["slope"],
        "offset": row["intercept"],
        "points": json.loads(row["points"]),
    }


def pressure_from_capacitance(capacitance, cal):
    return (capacitance - cal["offset"]) / cal["slope"]


@app.after_request
def add_cors_headers(resp):
    # Lets you host the frontend somewhere else (e.g. GitHub Pages) and
    # still call this API. Safe to leave open for a hackathon project;
    # tighten this if the station ever goes properly public.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/reading", methods=["POST", "OPTIONS"])
def post_reading():
    """Called by the ESP32 (or anything else) to push one new reading."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    if "capacitance" not in data:
        return jsonify({"error": "capacitance is required"}), 400

    try:
        capacitance = float(data["capacitance"])
    except (TypeError, ValueError):
        return jsonify({"error": "capacitance must be a number"}), 400

    # Any "pressure" in the payload is ignored on purpose - it is always
    # derived from capacitance using the saved calibration.
    humidity = data.get("humidity")
    temperature = data.get("temperature")
    lat = data.get("lat")
    lon = data.get("lon")
    ts = data.get("timestamp") or datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO readings (ts, capacitance, pressure, humidity, temperature, lat, lon) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (ts, capacitance, humidity, temperature, lat, lon),
        )

    return jsonify({"status": "ok"}), 201


@app.route("/api/readings", methods=["GET"])
def get_readings():
    """Called by the dashboard. Supports two ways of asking for data:

      /api/readings?since=<ISO timestamp>   readings from that time onward
                                             (used by the graph for its
                                             rolling 24-hour window)
      /api/readings?limit=<N>               most recent N readings
                                             (used by the CSV/JSON export
                                             to get everything on file)

    If neither is given, defaults to the most recent 200.
    Pressure in the response is calculated from the saved calibration.
    """
    since = request.args.get("since")
    limit = request.args.get("limit", type=int)
    if limit is None:
        limit = 100000 if since else 200

    query = "SELECT ts, capacitance, humidity, temperature, lat, lon FROM readings"
    params = []
    if since:
        query += " WHERE ts >= ?"
        params.append(since)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cal = load_calibration()
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    readings = []
    for row in reversed(rows):
        reading = dict(row)
        reading["pressure"] = pressure_from_capacitance(reading["capacitance"], cal)
        readings.append(reading)
    return jsonify(readings)


@app.route("/api/calibration", methods=["GET", "POST", "OPTIONS"])
def calibration():
    """Read or change the pressure<->capacitance relation  C = slope*dP + offset."""
    if request.method == "OPTIONS":
        return "", 204

    if request.method == "GET":
        return jsonify(load_calibration())

    data = request.get_json(silent=True) or {}
    try:
        slope = float(data["slope"])
        offset = float(data["offset"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "slope and offset must be numbers"}), 400
    if not (math.isfinite(slope) and math.isfinite(offset)) or slope == 0:
        return jsonify({"error": "slope must be non-zero and both values finite"}), 400

    points = data.get("points")
    if points is None:
        points = load_calibration()["points"]
    else:
        try:
            points = [[float(p), float(c)] for p, c in points]
        except (TypeError, ValueError):
            return jsonify({"error": "points must be a list of [pressure, capacitance] pairs"}), 400
        if not all(math.isfinite(v) for pair in points for v in pair):
            return jsonify({"error": "points must be finite numbers"}), 400

    with get_db() as conn:
        conn.execute(
            "UPDATE calibration SET slope = ?, intercept = ?, points = ? WHERE id = 1",
            (slope, offset, json.dumps(points)),
        )
    return jsonify({"slope": slope, "offset": offset, "points": points})


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def frontend_assets(filename):
    # Serves local frontend files (e.g. vendor/chart.umd.js) referenced
    # by relative path in index.html, so nothing depends on an external CDN.
    return send_from_directory(FRONTEND_DIR, filename)


# Create tables when the module loads, so this also works under
# `flask run` / gunicorn, not just `python app.py`.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
