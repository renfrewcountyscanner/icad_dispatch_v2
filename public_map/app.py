# public_map/app.py
"""
Public Live Map Application for iCAD Dispatch.

A completely separate Flask app that reads call data from iCAD's database
and broadcasts new calls in real-time via Flask-SocketIO.

Hardened for 5-50 concurrent public users behind a reverse proxy.
"""
import os
import json
import time
import psycopg2
import logging
import decimal
import requests
from datetime import datetime
from urllib.parse import urljoin
from functools import lru_cache

from flask import Flask, send_from_directory, request, render_template, Response
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

from lib.map_pin_renderer import MapPinRenderer, MapPinConfig

# Custom JSON encoder to handle PostgreSQL Decimal types
def sanitize_for_json(obj):
    """Recursively convert Decimal and other non-JSON-serializable types."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    return obj

load_dotenv()

# Database configuration (PostgreSQL only)
PG_HOST = os.environ.get("PG_HOST")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_DATABASE = os.environ.get("PG_DATABASE")
PG_USER = os.environ.get("PG_USER")
PG_PASSWORD = os.environ.get("PG_PASSWORD")

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
NOMINATIM_BASE_URL = os.environ.get("NOMINATIM_BASE_URL", "").rstrip("/")
PUBLIC_MAP_CORS_ORIGINS = [
    origin.strip() for origin in os.environ.get("PUBLIC_MAP_CORS_ORIGINS", BASE_URL).split(",")
    if origin.strip()
]
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is required. "
        "Set it in your .env file before starting the container."
    )

PUBLIC_MAP_API_KEY = os.environ.get("PUBLIC_MAP_API_KEY")
if not PUBLIC_MAP_API_KEY:
    raise RuntimeError(
        "PUBLIC_MAP_API_KEY environment variable is required. "
        "It must match the value set in the iCAD dispatch .env file."
    )
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
    cors_allowed_origins=PUBLIC_MAP_CORS_ORIGINS,
    ping_interval=25,
    ping_timeout=60,
    logger=False,
    engineio_logger=False,
    json=json,  # Use standard json module; we'll handle Decimal in the data itself
)

def _check_rate_limit():
    """Shared, restart-safe 60 request/minute limiter keyed by client IP."""
    try:
        rows = get_icad_db().query(
            """
            WITH expired AS (
                DELETE FROM public_map_rate_limits
                WHERE bucket_start < NOW() - INTERVAL '2 hours'
            ), allowed AS (
                INSERT INTO public_map_rate_limits (bucket_start, client_ip, request_count)
                VALUES (date_trunc('minute', NOW()), ?, 1)
                ON CONFLICT (bucket_start, client_ip) DO UPDATE
                    SET request_count = public_map_rate_limits.request_count + 1
                    WHERE public_map_rate_limits.request_count < 60
                RETURNING request_count
            ) SELECT request_count FROM allowed
            """,
            (request.remote_addr or "unknown",),
        )
        return bool(rows)
    except Exception as exc:
        logger.warning("Public-map rate limiter unavailable: %s", exc)
        return False


def _is_duplicate(call_id: int) -> bool:
    rows = get_icad_db().query(
        "SELECT 1 FROM public_map_broadcasts WHERE call_id = ?",
        (call_id,),
        fetch_mode="one",
    )
    return bool(rows)

def _mark_broadcast(call_id: int) -> None:
    get_icad_db().query(
        """
        INSERT INTO public_map_broadcasts (call_id, broadcast_epoch)
        VALUES (?, ?)
        ON CONFLICT (call_id) DO UPDATE SET broadcast_epoch = EXCLUDED.broadcast_epoch
        """,
        (call_id, int(time.time())),
    )


def _error(message: str, status: int = 400):
    return {"success": False, "message": message}, status


def _json_object(value):
    try:
        parsed = json.loads(value) if value else None
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def _parse_int(value: str | None, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return parsed


def _parse_float(value: str | None, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be at most {maximum:g}")
    return parsed


# ── DB helpers ─────────────────────────────────────────────────────

class PostgreSQLReadOnlyDB:
    def __init__(self, host, port, database, user, password):
        self.conn_params = {
            "host": host,
            "port": port,
            "dbname": database,
            "user": user,
            "password": password
        }

    def query(self, sql: str, params=(), fetch_mode="all"):
        # Translate SQLite ? to PostgreSQL %s
        sql = sql.replace('?', '%s')
        
        conn = psycopg2.connect(**self.conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql, params)
        
        # INSERT/UPDATE statements do not produce result rows.
        if not cur.description:
            cur.close()
            conn.close()
            return None if fetch_mode == "one" else []

        col_names = [desc[0] for desc in cur.description]
        if fetch_mode == "one":
            row = cur.fetchone()
            result = dict(zip(col_names, row)) if row else None
        else:
            rows = cur.fetchall()
            result = [dict(zip(col_names, row)) for row in rows] if rows else []
        
        cur.close()
        conn.close()
        return result


def get_icad_db():
    if not all([PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD]):
        raise RuntimeError("PostgreSQL environment variables (PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD) are required.")
    return PostgreSQLReadOnlyDB(PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD)


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


# ── On-demand map image renderer (zero persistent storage) ─────────

def _render_map_png(lat_key: str, lng_key: str, incident: str) -> bytes:
    """
    Map PNG generator — no caching so every request is fresh.
    """
    lat = float(lat_key)
    lng = float(lng_key)
    cfg = MapPinConfig()
    renderer = MapPinRenderer(config=cfg, logger=logger)
    return renderer.render_png(
        lat=lat,
        lon=lng,
        incident_category=incident,
    )


@app.route("/map-image")
def map_image():
    """
    Generate a static map PNG on demand.
    Query params:
      lat     (float, required)
      lng     (float, required)
      incident (str, optional, default "Other")
    """
    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    lat_raw = request.args.get("lat")
    lng_raw = request.args.get("lng")
    incident = request.args.get("incident", "Other").strip()

    if not lat_raw or not lng_raw:
        return {"success": False, "message": "lat and lng are required"}, 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return {"success": False, "message": "Invalid lat or lng"}, 400

    # Clamp to valid ranges
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return {"success": False, "message": "lat/lng out of bounds"}, 400

    # Round to 5 decimals (~1.1 m) for cache key stability
    lat_key = f"{lat:.5f}"
    lng_key = f"{lng:.5f}"

    try:
        png_bytes = _render_map_png(lat_key, lng_key, incident)
    except Exception as e:
        logger.warning("Map render failed: %s", e)
        return {"success": False, "message": "Map render failed"}, 500

    return Response(
        png_bytes,
        mimetype="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Content-Disposition": "inline",
        },
    )


@app.route("/api/calls")
def api_calls():
    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    db = get_icad_db()
    since_hours = request.args.get("hours", "24")
    from_epoch = request.args.get("from")
    to_epoch = request.args.get("to")
    after_epoch = request.args.get("after")
    system_id = request.args.get("system_id")
    incident = request.args.get("incident")

    try:
        parsed_from = _parse_float(from_epoch, "from", minimum=0)
        parsed_to = _parse_float(to_epoch, "to", minimum=0)
        parsed_after = _parse_float(after_epoch, "after", minimum=0)
        parsed_hours = _parse_float(since_hours, "hours", minimum=1, maximum=720)
        if parsed_from is not None and parsed_to is not None and parsed_to < parsed_from:
            raise ValueError("to must be on or after from")
    except ValueError as exc:
        return _error(str(exc))

    filters = []
    params = []
    meta = {"count": 0}

    # Time range handling (priority: from/to > after > hours)
    if from_epoch:
        try:
            since_epoch = parsed_from
            filters.append("cr.start_epoch_s >= ?")
            params.append(since_epoch)
            meta["from"] = since_epoch
        except ValueError:
            filters.append("cr.start_epoch_s >= ?")
            params.append(time.time() - (24 * 3600))
    elif after_epoch:
        try:
            since_epoch = parsed_after
            filters.append("cr.start_epoch_s >= ?")
            params.append(since_epoch)
            meta["after"] = since_epoch
        except ValueError:
            filters.append("cr.start_epoch_s >= ?")
            params.append(time.time() - (24 * 3600))
    else:
        try:
            hours = parsed_hours
        except ValueError:
            return _error("hours must be a number")
        filters.append("cr.start_epoch_s >= ?")
        params.append(time.time() - (hours * 3600))
        meta["hours"] = hours

    if to_epoch:
        try:
            until_epoch = parsed_to
            filters.append("cr.start_epoch_s <= ?")
            params.append(until_epoch)
            meta["to"] = until_epoch
        except ValueError:
            return _error("alert_trigger_ids must be a comma-separated list of integers")

    try:
        parsed_system_id = _parse_int(system_id, "system_id", minimum=1)
    except ValueError as exc:
        return _error(str(exc))
    if parsed_system_id:
        filters.append("cr.radio_system_id = ?")
        params.append(parsed_system_id)

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

    # ── Dynamic LIMIT: wider ranges need more room, hard cap at 5000 ──
    now = time.time()
    if from_epoch and to_epoch:
        try:
            range_seconds = float(to_epoch) - float(from_epoch)
        except (TypeError, ValueError):
            range_seconds = 24 * 3600
    elif after_epoch:
        try:
            range_seconds = now - float(after_epoch)
        except (TypeError, ValueError):
            range_seconds = 24 * 3600
    else:
        try:
            range_seconds = float(since_hours) * 3600
        except (TypeError, ValueError):
            range_seconds = 24 * 3600

    if range_seconds <= 86400:          # <= 1 day
        limit = 500
    elif range_seconds <= 604800:       # <= 7 days
        limit = 1500
    elif range_seconds <= 2592000:      # <= 30 days
        limit = 3000
    else:
        limit = 5000
    meta["limit"] = limit

    # Fetch limit + 1 to detect truncation without a separate COUNT query
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
        LIMIT {limit + 1}
    """

    rows = db.query(sql, tuple(params))
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    meta["truncated"] = truncated
    result = []
    for r in rows:
        lat = lng = None
        address = ""
        is_corrected = False

        # ── lat/lng for map pin (independent of display address) ──
        if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
            lat = float(r["corrected_lat"])
            lng = float(r["corrected_lon"])
            is_corrected = True
        elif r.get("address_geocoded_json"):
            try:
                geo = json.loads(r["address_geocoded_json"])
                lat = geo.get("lat")
                lng = geo.get("lng")
            except Exception:
                pass

        # ── display address: prefer extracted (has house number), then geocoded ──
        if r.get("corrected_address"):
            address = r["corrected_address"]
        elif r.get("address_extracted_json"):
            try:
                ext = json.loads(r["address_extracted_json"])
                address = ext.get("raw_text") or ""
                if not address:
                    parts = []
                    for key in ("street", "city", "county", "state"):
                        if ext.get(key):
                            parts.append(str(ext[key]))
                    if parts:
                        address = ", ".join(parts)
            except Exception:
                pass
        if not address and r.get("address_geocoded_json"):
            try:
                geo = json.loads(r["address_geocoded_json"])
                address = geo.get("formatted_address") or ""
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

    meta["count"] = len(result)
    resp = sanitize_for_json({"success": True, "result": result, "meta": meta})
    return Response(
        json.dumps(resp),
        mimetype="application/json",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.route("/api/triggers")
def api_triggers():
    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    db = get_icad_db()
    system_id = request.args.get("system_id")

    sql = """
        SELECT
            at.alert_trigger_id,
            at.alert_trigger_name,
            at.radio_system_id
        FROM alert_triggers at
        WHERE at.alert_trigger_enabled = 1
    """
    params = []
    try:
        parsed_system_id = _parse_int(system_id, "system_id", minimum=1)
    except ValueError as exc:
        return _error(str(exc))
    if parsed_system_id:
        sql += " AND at.radio_system_id = ?"
        params.append(parsed_system_id)

    sql += " ORDER BY at.alert_trigger_name"

    rows = db.query(sql, tuple(params))
    result = [
        {
            "alert_trigger_id": r["alert_trigger_id"],
            "alert_trigger_name": r["alert_trigger_name"] or f"Trigger {r['alert_trigger_id']}",
            "radio_system_id": r["radio_system_id"],
        }
        for r in rows
    ]
    resp = {"success": True, "result": result}
    return Response(
        json.dumps(resp),
        mimetype="application/json",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.route("/api/geocode/search")
def api_geocode_search():
    """Search the deployment's local Nominatim instance without browser CORS exposure."""
    if not _check_rate_limit():
        return _error("Rate limit exceeded", 429)

    query = (request.args.get("q") or "").strip()
    if not NOMINATIM_BASE_URL:
        return _error("Local address search is not configured", 503)
    if len(query) < 3:
        return _error("Search text must be at least 3 characters")
    if len(query) > 200:
        return _error("Search text is too long")

    try:
        rows = None
        for endpoint in ("search", "search.php"):
            response = requests.get(
                urljoin(f"{NOMINATIM_BASE_URL}/", endpoint),
                params={"format": "jsonv2", "limit": 5, "q": query},
                headers={"Accept-Language": "en"},
                timeout=5,
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            rows = response.json()
            break
        if rows is None:
            raise requests.HTTPError("No supported Nominatim search endpoint found")
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Local Nominatim search failed: %s", exc)
        return _error("Local address search is unavailable", 503)

    return {"success": True, "result": [
        {"display_name": row.get("display_name", ""), "lat": row.get("lat"), "lng": row.get("lon")}
        for row in rows if row.get("lat") is not None and row.get("lon") is not None
    ]}


@app.route("/api/push-call", methods=["POST"])
def api_push_call():
    """
    Receive a fully-baked call from iCAD dispatch and broadcast it
    via SocketIO to all connected browsers.
    Requires X-API-Key header matching PUBLIC_MAP_API_KEY.
    """
    # API key check — fail closed for security
    api_key = request.headers.get("X-API-Key")
    if not PUBLIC_MAP_API_KEY or api_key != PUBLIC_MAP_API_KEY:
        logger.warning("Unauthorized push attempt from %s", request.remote_addr)
        return {"success": False, "message": "Unauthorized"}, 401

    if not _check_rate_limit():
        return {"success": False, "message": "Rate limit exceeded"}, 429

    try:
        data = request.get_json(force=True, silent=True) or {}
        calls = data.get("calls", [])
        if not calls:
            return {"success": False, "message": "No calls in payload"}, 400

        valid_calls = []
        for call in calls:
            if call and call.get("call_id") and call.get("timestamp") is not None:
                valid_calls.append(call)
                _mark_broadcast(int(call["call_id"]))

        if valid_calls:
            socketio.emit("new_calls", sanitize_for_json({"calls": valid_calls}))
            logger.info("Broadcast %d pushed call(s) from iCAD", len(valid_calls))

        return {"success": True, "broadcasted": len(valid_calls)}
    except Exception as e:
        logger.error("Push call error: %s", e)
        return {"success": False, "message": str(e)}, 500


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

    # ── lat/lng for map pin (independent of display address) ──
    if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
        lat = float(r["corrected_lat"])
        lng = float(r["corrected_lon"])
        is_corrected = True
    elif r.get("address_geocoded_json"):
        try:
            geo = json.loads(r["address_geocoded_json"])
            lat = geo.get("lat")
            lng = geo.get("lng")
        except Exception:
            pass

    # ── display address: prefer extracted (has house number), then geocoded ──
    if r.get("corrected_address"):
        address = r["corrected_address"]
    elif r.get("address_extracted_json"):
        try:
            ext = json.loads(r["address_extracted_json"])
            address = ext.get("raw_text") or ""
            if not address:
                parts = []
                for key in ("street", "city", "county", "state"):
                    if ext.get(key):
                        parts.append(str(ext[key]))
                if parts:
                    address = ", ".join(parts)
        except Exception:
            pass
    if not address and r.get("address_geocoded_json"):
        try:
            geo = json.loads(r["address_geocoded_json"])
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

    resp = sanitize_for_json({
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
            "location_source": "manual correction" if is_corrected else ("automatic geocode" if lat is not None and lng is not None else "no resolved location"),
            "address_extracted": _json_object(r.get("address_extracted_json")),
            "address_geocoded": _json_object(r.get("address_geocoded_json")),
        }
    })
    return Response(
        json.dumps(resp),
        mimetype="application/json",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.route("/audio/<path:audio_path>")
def serve_audio(audio_path: str):
    # Normalize and reject any path containing parent-directory references
    safe_path = audio_path.lstrip("/")
    norm = os.path.normpath(safe_path)
    if norm != safe_path or ".." in norm.split(os.sep):
        return {"success": False, "message": "Invalid audio path"}, 400
    return send_from_directory(AUDIO_ROOT, safe_path)


# ── SocketIO Events ────────────────────────────────────────────────

@socketio.on("connect")
def handle_connect():
    emit("connected", {"message": "Live map connected"})


@socketio.on("subscribe")
def handle_subscribe(data):
    resp = {}
    if "hours" in data:
        try:
            resp["hours"] = float(data["hours"])
        except (TypeError, ValueError):
            resp["hours"] = 24.0
    if "from" in data:
        try:
            resp["from"] = float(data["from"])
        except (TypeError, ValueError):
            pass
    if "to" in data:
        try:
            resp["to"] = float(data["to"])
        except (TypeError, ValueError):
            pass
    if not resp:
        resp["hours"] = 24.0
    emit("subscribed", resp)


# ── Background polling ───────────────────────────────────────────────

def _process_call_row(r):
    """Helper: build a call dict from a raw DB row."""
    lat = lng = None
    address = ""
    is_corrected = False

    # ── lat/lng for map pin (independent of display address) ──
    if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
        lat = float(r["corrected_lat"])
        lng = float(r["corrected_lon"])
        is_corrected = True
    elif r.get("address_geocoded_json"):
        try:
            geo = json.loads(r["address_geocoded_json"])
            lat = geo.get("lat")
            lng = geo.get("lng")
        except Exception:
            pass

    # ── display address: prefer extracted (has house number), then geocoded ──
    if r.get("corrected_address"):
        address = r["corrected_address"]
    elif r.get("address_extracted_json"):
        try:
            ext = json.loads(r["address_extracted_json"])
            address = ext.get("raw_text") or ""
            if not address:
                parts = []
                for key in ("street", "city", "county", "state"):
                    if ext.get(key):
                        parts.append(str(ext[key]))
                if parts:
                    address = ", ".join(parts)
        except Exception:
            pass
    if not address and r.get("address_geocoded_json"):
        try:
            geo = json.loads(r["address_geocoded_json"])
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
    """Inner loop: polls DB and emits new calls.

    Only broadcasts calls that are at least POLLER_MIN_AGE seconds old
    so that iCAD has time to finish transcription + geocoding before
    the poller picks them up. Real-time delivery is handled by the
    iCAD push endpoint; the poller is purely for catch-up.
    """
    last_max_id = 0
    POLLER_MIN_AGE = 120  # seconds: poller is catch-up only; iCAD push handles real-time

    while True:
        try:
            db = get_icad_db()
            rows = db.query(
                "SELECT MAX(call_id) as max_id FROM call_records",
                fetch_mode="one"
            )
            current_max = rows["max_id"] if rows and rows["max_id"] else 0

            if current_max > last_max_id:
                cutoff = int(time.time()) - POLLER_MIN_AGE
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
                    WHERE cr.call_id > ? AND cr.start_epoch_s < ?
                        AND (ct.address_extracted_json IS NOT NULL OR ct.address_geocoded_json IS NOT NULL)
                    ORDER BY cr.call_id DESC
                    LIMIT 10
                    """,
                    (last_max_id, cutoff)
                )

                calls = [_process_call_row(r) for r in new_rows]
                calls = [c for c in calls if c and not _is_duplicate(c["call_id"])]

                if calls:
                    for c in calls:
                        _mark_broadcast(c["call_id"])
                    socketio.emit("new_calls", sanitize_for_json({"calls": calls}))
                    logger.info("Broadcast %d new call(s) from poller", len(calls))

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
