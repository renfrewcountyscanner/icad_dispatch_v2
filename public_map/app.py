# public_map/app.py
"""
Public Live Map Application for iCAD Dispatch.

A completely separate Flask app that reads call data from iCAD's SQLite database
in read-only mode and broadcasts new calls in real-time via Flask-SocketIO.
"""
import os
import json
import time
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

load_dotenv()

ICAD_DB_PATH = os.environ.get("ICAD_DB_PATH", "/app/shared_var/icad_dispatch.db")
MAP_DB_PATH = os.environ.get("MAP_DB_PATH", "/app/shared_var/map_app.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
AUDIO_ROOT = "/app/static/audio"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = SECRET_KEY
app.config["JSON_SORT_KEYS"] = False

socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

# ── DB helpers ─────────────────────────────────────────────────────

class ReadOnlyDB:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def query(self, sql: str, params=(), fetch_mode="all"):
        conn = sqlite3.connect(f"{self.db_path}?mode=ro", uri=True)
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
    return app.send_static_file("map.html")


@app.route("/api/calls")
def api_calls():
    db = get_icad_db()
    since_hours = request.args.get("hours", "24")
    system_id = request.args.get("system_id")
    incident = request.args.get("incident")
    talkgroup = request.args.get("talkgroup")

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

    if talkgroup:
        filters.append("(cr.talkgroup = ? OR cr.talkgroup_name LIKE ?)")
        params.extend([talkgroup, f"%{talkgroup}%"])

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


@app.route("/api/calls/<int:call_id>")
def api_call_detail(call_id: int):
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

def background_poller():
    """Background greenlet: polls iCAD DB every 5s and broadcasts new calls."""
    last_max_id = 0

    while True:
        try:
            db = get_icad_db()
            # Get the most recent call IDs
            rows = db.query(
                "SELECT MAX(call_id) as max_id FROM call_records",
                fetch_mode="one"
            )
            current_max = rows["max_id"] if rows and rows["max_id"] else 0

            if current_max > last_max_id:
                # Fetch the new calls
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

                calls = []
                for r in new_rows:
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

                    calls.append({
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
                    })

                if calls:
                    socketio.emit("new_calls", {"calls": calls})

                last_max_id = current_max

        except Exception as e:
            print(f"[Poller] Error: {e}")

        socketio.sleep(5)


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
else:
    # When running under gunicorn, start the background task
    @socketio.on("connect")
    def _start_poller_on_first_connect():
        if not hasattr(_start_poller_on_first_connect, "started"):
            _start_poller_on_first_connect.started = True
            socketio.start_background_task(background_poller)
