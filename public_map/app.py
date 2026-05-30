# public_map/app.py
"""
Public Live Map Application for iCAD Dispatch.

A completely separate Flask app that reads call data from iCAD's SQLite database
and broadcasts new calls in real-time via Flask-SocketIO.

Hardened for 5-50 concurrent public users behind a reverse proxy.
"""
import os
import json
import time
import sqlite3
import logging
from datetime import datetime
from collections import defaultdict

from flask import Flask, send_from_directory, request, render_template
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

load_dotenv()

ICAD_DB_PATH = os.environ.get("ICAD_DB_PATH", "/app/shared_var/icad_dispatch.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
AUDIO_ROOT = "/app/static/audio"

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("public_map")

# ── Flask + SocketIO ───────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = SECRET_KEY
app.config["JSON_SORT_KEYS"] = False

socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins="*",
    ping_interval=25,
    ping_timeout=60,
    logger=False,
    engineio_logger=False,
)

# ── Rate limiting (in-memory, per-IP) ──────────────────────────────
# 60 requests per minute per IP
_rate_limit_buckets = defaultdict(list)

def _check_rate_limit():
    ip = request.remote_addr or "unknown"
    now = time.time()
    window = 60  # seconds
    max_requests = 60
    bucket = _rate_limit_buckets[ip]
    # Prune old entries
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= max_requests:
        return False
    bucket.append(now)
    return True


# ── DB helpers ─────────────────────────────────────────────────────

class ReadOnlyDB:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def query(self, sql: str, params=(), fetch_mode="all"):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetch_mode == "one":
            row = cur.fetchone()
            result = dict(row) if row else None
        else:
            result = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return result


def get_icad_db():
    return ReadOnlyDB(ICAD_DB_PATH)


# ── Routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("map.html")


@app.route("/health")
def health():
    """Health check for reverse proxy monitoring."""
    try:
        db = get_icad_db()
        count = db.query("SELECT COUNT(*) as c FROM call_records", fetch_mode="one")["c"]
        return {
            "status": "ok",
            "calls_total": count,
            "timestamp": int(time.time()),
        }
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return {"status": "error", "message": str(e)}, 503


@app.route("/api/calls")
def api_calls():
    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    db = get_icad_db()
    since_hours = request.args.get("hours", "24")
    system_id = request.args.get("system_id")
    incident = request.args.get("incident")

    try:
        hours = float(since_hours)
    except ValueError:
        hours = 24.0

    since_epoch = time.time() - (hours * 3600)

    filters = ["cr.start_epoch_s >= ?"]
    params = [since_epoch]

    if system_id:
        filters.append("cr.radio_system_id = ?")
        params.append(int(system_id))

    if incident:
        filters.append("ct.incident_category = ?")
        params.append(incident)

    alert_trigger_ids = request.args.get("alert_trigger_ids")
    if alert_trigger_ids:
        try:
            trig_ids = [int(x.strip()) for x in alert_trigger_ids.split(",") if x.strip()]
            if trig_ids:
                placeholders = ",".join("?" for _ in trig_ids)
                filters.append(f"cr.call_id IN (SELECT call_id FROM trigger_fires WHERE alert_trigger_id IN ({placeholders}))")
                params.extend(trig_ids)
        except ValueError:
            pass

    where_clause = " AND ".join(filters)

    sql = f"""
        SELECT
            cr.call_id,
            cr.start_epoch_s,
            cr.duration_s,
            cr.talkgroup,
            cr.talkgroup_name,
            cr.radio_system_id,
            rs.system_name,
            ct.text_full,
            ct.address_extracted_json,
            ct.address_geocoded_json,
            ct.incident_category,
            cc.corrected_lat,
            cc.corrected_lon,
            cc.corrected_address,
            cc.notes AS correction_notes
        FROM call_records cr
        LEFT JOIN call_transcripts ct ON cr.call_id = ct.call_id
        LEFT JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
        LEFT JOIN call_corrections cc ON cr.call_id = cc.call_id
        WHERE {where_clause}
        ORDER BY cr.start_epoch_s DESC
        LIMIT 2000
    """

    rows = db.query(sql, tuple(params))
    result = []
    for r in rows:
        lat = lng = None
        address = ""
        is_corrected = False

        if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
            lat = float(r["corrected_lat"])
            lng = float(r["corrected_lon"])
            address = r.get("corrected_address") or ""
            is_corrected = True
        elif r.get("address_geocoded_json"):
            try:
                geo = json.loads(r["address_geocoded_json"])
                lat = geo.get("lat")
                lng = geo.get("lng")
                address = geo.get("formatted_address") or ""
            except Exception:
                pass

        # Fallback to extracted address
        if not address and r.get("address_extracted_json"):
            try:
                ext = json.loads(r["address_extracted_json"])
                parts = []
                for key in ("street", "city", "county", "state"):
                    if ext.get(key):
                        parts.append(str(ext[key]))
                if parts:
                    address = ", ".join(parts)
            except Exception:
                pass

        # Build audio URL
        audio_url = ""
        if r.get("file_path"):
            fp = r["file_path"].strip()
            if fp.startswith("http"):
                audio_url = fp
            else:
                audio_url = f"{BASE_URL}/audio/{fp.replace('static/audio/', '')}"

        result.append({
            "call_id": r["call_id"],
            "timestamp": r["start_epoch_s"],
            "datetime": datetime.fromtimestamp(r["start_epoch_s"]).strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": r["duration_s"],
            "talkgroup": r.get("talkgroup") or "",
            "talkgroup_name": r.get("talkgroup_name") or "",
            "system_id": r.get("radio_system_id"),
            "system_name": r.get("system_name") or "",
            "transcript": (r.get("text_full") or "").strip(),
            "incident_category": r.get("incident_category") or "Other",
            "lat": lat,
            "lng": lng,
            "address": address,
            "audio_url": audio_url,
            "has_location": lat is not None and lng is not None,
            "is_corrected": is_corrected,
            "correction_notes": r.get("correction_notes") or "",
        })

    return {"success": True, "result": result, "meta": {"hours": hours, "count": len(result)}}


@app.route("/api/triggers")
def api_triggers():
    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    db = get_icad_db()
    system_id = request.args.get("system_id")

    sql = """
        SELECT
            alert_trigger_id,
            alert_trigger_name,
            radio_system_id
        FROM alert_triggers
        WHERE alert_trigger_enabled = 1
    """
    params = []
    if system_id:
        sql += " AND radio_system_id = ?"
        params.append(int(system_id))

    sql += " ORDER BY alert_trigger_name"

    rows = db.query(sql, tuple(params))
    result = [
        {
            "alert_trigger_id": r["alert_trigger_id"],
            "alert_trigger_name": r["alert_trigger_name"] or f"Trigger {r['alert_trigger_id']}",
            "radio_system_id": r["radio_system_id"],
        }
        for r in rows
    ]
    return {"success": True, "result": result}


@app.route("/api/calls/<int:call_id>")
def api_call_detail(call_id: int):
    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    db = get_icad_db()
    sql = """
        SELECT
            cr.call_id, cr.start_epoch_s, cr.duration_s,
            cr.talkgroup, cr.talkgroup_name, cr.radio_system_id,
            rs.system_name,
            ct.text_full, ct.address_extracted_json, ct.address_geocoded_json,
            ct.incident_category,
            cc.corrected_lat, cc.corrected_lon, cc.corrected_address, cc.notes AS correction_notes
        FROM call_records cr
        LEFT JOIN call_transcripts ct ON cr.call_id = ct.call_id
        LEFT JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
        LEFT JOIN call_corrections cc ON cr.call_id = cc.call_id
        WHERE cr.call_id = ?
    """
    rows = db.query(sql, (call_id,), fetch_mode="all")
    if not rows:
        return {"success": False, "message": "Call not found"}, 404

    r = rows[0]
    lat = lng = None
    address = ""
    is_corrected = False

    if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
        lat = float(r["corrected_lat"])
        lng = float(r["corrected_lon"])
        address = r.get("corrected_address") or ""
        is_corrected = True
    elif r.get("address_geocoded_json"):
        try:
            geo = json.loads(r["address_geocoded_json"])
            lat = geo.get("lat")
            lng = geo.get("lng")
            address = geo.get("formatted_address") or ""
        except Exception:
            pass

    audio_url = ""
    if r.get("file_path"):
        fp = r["file_path"].strip()
        if fp.startswith("http"):
            audio_url = fp
        else:
            audio_url = f"{BASE_URL}/audio/{fp.replace('static/audio/', '')}"

    return {
        "success": True,
        "result": {
            "call_id": r["call_id"],
            "timestamp": r["start_epoch_s"],
            "datetime": datetime.fromtimestamp(r["start_epoch_s"]).strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": r["duration_s"],
            "talkgroup": r.get("talkgroup") or "",
            "talkgroup_name": r.get("talkgroup_name") or "",
            "system_id": r.get("radio_system_id"),
            "system_name": r.get("system_name") or "",
            "transcript": (r.get("text_full") or "").strip(),
            "incident_category": r.get("incident_category") or "Other",
            "lat": lat,
            "lng": lng,
            "address": address,
            "audio_url": audio_url,
            "has_location": lat is not None and lng is not None,
            "is_corrected": is_corrected,
            "correction_notes": r.get("correction_notes") or "",
        }
    }


@app.route("/audio/<path:audio_path>")
def serve_audio(audio_path: str):
    safe_path = audio_path.lstrip("/")
    full_path = os.path.join(AUDIO_ROOT, safe_path)
    if not os.path.isfile(full_path):
        return {"success": False, "message": "Audio not found"}, 404
    return send_from_directory(AUDIO_ROOT, safe_path)


# ── SocketIO Events ────────────────────────────────────────────────

@socketio.on("connect")
def handle_connect():
    emit("connected", {"message": "Live map connected"})


@socketio.on("subscribe")
def handle_subscribe(data):
    hours = data.get("hours", 24)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        hours = 24.0
    emit("subscribed", {"hours": hours})


# ── Background polling ───────────────────────────────────────────────

def _process_call_row(r):
    """Helper: build a call dict from a raw DB row."""
    lat = lng = None
    address = ""
    is_corrected = False

    if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
        lat = float(r["corrected_lat"])
        lng = float(r["corrected_lon"])
        address = r.get("corrected_address") or ""
        is_corrected = True
    elif r.get("address_geocoded_json"):
        try:
            geo = json.loads(r["address_geocoded_json"])
            lat = geo.get("lat")
            lng = geo.get("lng")
            address = geo.get("formatted_address") or ""
        except Exception:
            pass

    audio_url = ""
    if r.get("file_path"):
        fp = r["file_path"].strip()
        if fp.startswith("http"):
            audio_url = fp
        else:
            audio_url = f"{BASE_URL}/audio/{fp.replace('static/audio/', '')}"

    return {
        "call_id": r["call_id"],
        "timestamp": r["start_epoch_s"],
        "datetime": datetime.fromtimestamp(r["start_epoch_s"]).strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": r["duration_s"],
        "talkgroup": r.get("talkgroup") or "",
        "talkgroup_name": r.get("talkgroup_name") or "",
        "system_name": r.get("system_name") or "",
        "transcript": (r.get("text_full") or "").strip(),
        "incident_category": r.get("incident_category") or "Other",
        "lat": lat,
        "lng": lng,
        "address": address,
        "audio_url": audio_url,
        "has_location": lat is not None and lng is not None,
        "is_corrected": is_corrected,
    }


def _poller_loop():
    """Inner loop: polls DB and emits new calls."""
    last_max_id = 0
    while True:
        try:
            db = get_icad_db()
            rows = db.query(
                "SELECT MAX(call_id) as max_id FROM call_records",
                fetch_mode="one"
            )
            current_max = rows["max_id"] if rows and rows["max_id"] else 0

            if current_max > last_max_id:
                new_rows = db.query(
                    """
                    SELECT
                        cr.call_id, cr.start_epoch_s, cr.duration_s,
                        cr.talkgroup, cr.talkgroup_name, cr.radio_system_id,
                        rs.system_name,
                        ct.text_full, ct.address_extracted_json, ct.address_geocoded_json,
                        ct.incident_category,
                        cc.corrected_lat, cc.corrected_lon, cc.corrected_address
                    FROM call_records cr
                    LEFT JOIN call_transcripts ct ON cr.call_id = ct.call_id
                    LEFT JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
                    LEFT JOIN call_corrections cc ON cr.call_id = cc.call_id
                    WHERE cr.call_id > ?
                    ORDER BY cr.call_id DESC
                    LIMIT 10
                    """,
                    (last_max_id,)
                )

                calls = [_process_call_row(r) for r in new_rows]
                calls = [c for c in calls if c]  # filter None

                if calls:
                    socketio.emit("new_calls", {"calls": calls})
                    logger.info("Broadcast %d new call(s)", len(calls))

                last_max_id = current_max

        except Exception as e:
            logger.error("Poller loop error: %s", e, exc_info=True)

        socketio.sleep(5)


def background_poller():
    """
    Outer wrapper: ensures the poller never dies permanently.
    If _poller_loop crashes, log the error and restart after 10s.
    """
    while True:
        try:
            logger.info("Starting background poller")
            _poller_loop()
        except Exception as e:
            logger.critical("Poller crashed, restarting in 10s: %s", e, exc_info=True)
            time.sleep(10)


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
else:
    # When running under gunicorn, start the background task once
    @socketio.on("connect")
    def _start_poller_on_first_connect():
        if not hasattr(_start_poller_on_first_connect, "started"):
            _start_poller_on_first_connect.started = True
            socketio.start_background_task(background_poller)
