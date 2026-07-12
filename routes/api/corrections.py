# routes/api/corrections.py
"""
API endpoints for call location corrections.
Allows admins to manually correct the geolocation of calls.
"""
import json
import time
from flask import Blueprint, request, jsonify, current_app
from routes.decorators import login_required, csrf_protect

bp_corrections = Blueprint("api_corrections", __name__)


@bp_corrections.route("/calls/needs-correction", methods=["GET"])
@login_required
def list_needs_correction():
    """List calls from the last 72h that have no geocoded location or have a correction."""
    db = current_app.config["db"]
    logger = current_app.config["logger"]

    status = request.args.get("status", "all")
    if status not in {"all", "no_location", "corrected"}:
        return _err("invalid status", 400)

    try:
        limit = max(1, min(int(request.args.get("limit", 500)), 500))
    except ValueError:
        return _err("limit must be an integer", 400)

    since = int(time.time()) - (72 * 3600)

    sql = """
        SELECT cr.call_id,
               cr.start_epoch_s,
               cr.duration_s,
               cr.talkgroup,
               cr.talkgroup_name,
               ct.text_full,
               ct.address_extracted_json,
               ct.address_geocoded_json,
               ct.incident_category,
               rs.system_name,
               cc.corrected_lat,
               cc.corrected_lon,
               cc.corrected_address,
               cc.notes,
               CASE WHEN cc.call_id IS NOT NULL THEN 1 ELSE 0 END AS has_correction
        FROM   call_records cr
        LEFT   JOIN call_transcripts ct ON cr.call_id = ct.call_id
        LEFT   JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
        LEFT   JOIN call_corrections cc ON cr.call_id = cc.call_id
        WHERE  cr.start_epoch_s >= ?
          AND  (? != 'corrected' OR cc.call_id IS NOT NULL)
        ORDER  BY cr.start_epoch_s DESC
        LIMIT  1000
    """
    res = db.execute_query(sql, (since, status), fetch_mode="all")
    if not res["success"]:
        logger.error("needs-correction query failed: %s", res["message"])
        return _err("DB error", 500)

    rows = []
    for r in res["result"]:
        # Determine if this call has a usable location
        has_location = False
        lat = lng = None
        address = ""

        if r.get("corrected_lat") is not None and r.get("corrected_lon") is not None:
            has_location = True
            lat = r["corrected_lat"]
            lng = r["corrected_lon"]
            address = r.get("corrected_address") or ""
        elif r.get("address_geocoded_json"):
            try:
                geo = json.loads(r["address_geocoded_json"])
                if geo.get("lat") and geo.get("lng"):
                    has_location = True
                    lat = geo["lat"]
                    lng = geo["lng"]
                    address = geo.get("formatted_address") or ""
            except Exception:
                pass

        if status == "no_location" and has_location:
            continue

        rows.append({
            "call_id": r["call_id"],
            "start_epoch": r["start_epoch_s"],
            "duration_s": r["duration_s"],
            "talkgroup": r.get("talkgroup") or "",
            "talkgroup_name": r.get("talkgroup_name") or "",
            "system_name": r.get("system_name") or "",
            "incident_category": r.get("incident_category") or "",
            "transcript": (r.get("text_full") or "").strip(),
            "has_location": has_location,
            "lat": lat,
            "lng": lng,
            "address": address,
            "has_correction": bool(r.get("has_correction")),
            "notes": r.get("notes") or "",
        })

        if len(rows) >= limit:
            break

    return jsonify(success=True, result=rows)


@bp_corrections.route("/calls/<int:call_id>/correct-location", methods=["POST"])
@login_required
@csrf_protect
def save_correction(call_id: int):
    """Save or update a manual location correction for a call."""
    db = current_app.config["db"]
    logger = current_app.config["logger"]

    body = request.get_json(force=True) or {}
    lat = body.get("lat")
    lng = body.get("lng")
    address = (body.get("address") or "").strip()
    notes = (body.get("notes") or "").strip()

    if lat is None or lng is None:
        return _err("lat and lng are required", 400)
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return _err("invalid lat/lng", 400)

    # Verify call exists
    exists = db.execute_query(
        "SELECT 1 FROM call_records WHERE call_id = ?", (call_id,), fetch_mode="one"
    )
    if not exists["success"] or not exists["result"]:
        return _err("call_id not found", 404)

    username = request.cookies.get("remember_me_username") or "admin"

    # Upsert: delete existing then insert
    db.execute_commit("DELETE FROM call_corrections WHERE call_id = ?", (call_id,))
    ins = db.execute_commit(
        """
        INSERT INTO call_corrections
            (call_id, corrected_lat, corrected_lon, corrected_address, notes, corrected_by, corrected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (call_id, lat_f, lng_f, address, notes, username, int(time.time()))
    )
    if not ins["success"]:
        logger.error("save correction failed: %s", ins["message"])
        return _err("DB error", 500)

    # ── Update transcript text to reflect corrected address ─────────────────
    _update_transcript_with_correction(db, call_id, address, logger)

    # ── Re-push to public map so live viewers see the corrected text ──────
    _push_corrected_call_to_public_map(db, call_id, logger)

    logger.info("Location corrected for call_id=%s by %s", call_id, username)
    return jsonify(success=True, result={"call_id": call_id, "lat": lat_f, "lng": lng_f})


@bp_corrections.route("/calls/<int:call_id>/correct-location", methods=["DELETE"])
@login_required
@csrf_protect
def delete_correction(call_id: int):
    """Remove a manual location correction, reverting to automatic geocoding."""
    db = current_app.config["db"]
    logger = current_app.config["logger"]

    res = db.execute_commit(
        "DELETE FROM call_corrections WHERE call_id = ?", (call_id,), return_count=True
    )
    if not res["success"]:
        logger.error("delete correction failed: %s", res["message"])
        return _err("DB error", 500)

    # ── Revert transcript text to original (from raw_json) ────────────────
    _revert_transcript(db, call_id, logger)

    # ── Re-push to public map so live viewers see the reverted text ───────
    _push_corrected_call_to_public_map(db, call_id, logger)

    return jsonify(success=True, result={"deleted": res.get("count", 0)})


# ───────────────────────── helpers ─────────────────────────

def _update_transcript_with_correction(db, call_id: int, corrected_address: str, logger):
    """Replace the extracted address in transcript text with the corrected one."""
    try:
        res = db.execute_query(
            "SELECT text_full, address_extracted_json, raw_json FROM call_transcripts WHERE call_id = ?",
            (call_id,), fetch_mode="one"
        )
        if not res["success"] or not res["result"]:
            return
        row = res["result"]
        text_full = row.get("text_full") or ""
        addr_json = row.get("address_extracted_json")
        if not text_full or not addr_json:
            return
        addr = json.loads(addr_json)
        raw_text = (addr.get("raw_text") or "").strip()
        if not raw_text:
            return
        # Case-insensitive replacement (first occurrence only)
        updated = _replace_first_ci(text_full, raw_text, corrected_address)
        if updated != text_full:
            db.execute_commit(
                "UPDATE call_transcripts SET text_full = ? WHERE call_id = ?",
                (updated, call_id)
            )
            logger.info("Updated transcript text for call_id=%s (address corrected)", call_id)
    except Exception as e:
        logger.warning("Transcript update on correction failed for call_id=%s: %s", call_id, e)


def _revert_transcript(db, call_id: int, logger):
    """Restore original transcript text from raw_json when correction is deleted."""
    try:
        res = db.execute_query(
            "SELECT raw_json FROM call_transcripts WHERE call_id = ?",
            (call_id,), fetch_mode="one"
        )
        if not res["success"] or not res["result"]:
            return
        raw_json = res["result"].get("raw_json")
        if not raw_json:
            return
        # raw_json is the original Whisper response; extract text from it
        raw = json.loads(raw_json)
        original_text = (raw.get("text") or "").strip()
        if original_text:
            db.execute_commit(
                "UPDATE call_transcripts SET text_full = ? WHERE call_id = ?",
                (original_text, call_id)
            )
            logger.info("Reverted transcript text for call_id=%s (correction deleted)", call_id)
    except Exception as e:
        logger.warning("Transcript revert failed for call_id=%s: %s", call_id, e)


def _replace_first_ci(text: str, old: str, new: str) -> str:
    """Replace first case-insensitive occurrence of `old` with `new`."""
    import re
    pattern = re.escape(old)
    return re.sub(pattern, new, text, count=1, flags=re.IGNORECASE)


def _push_corrected_call_to_public_map(db, call_id: int, logger):
    """Re-push a single call to the public_map so live viewers see corrections."""
    try:
        import os, requests
        public_map_url = os.environ.get("PUBLIC_MAP_URL", "http://public_map:5000/api/push-call")
        sql = """
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
            WHERE cr.call_id = ?
        """
        res = db.execute_query(sql, (call_id,), fetch_mode="all")
        if not res["success"]:
            return
        rows = res.get("result", [])
        if not rows:
            return
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
        if not address and r.get("address_extracted_json"):
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
        audio_url = ""
        fp = (r.get("file_path") or "").strip()
        if fp.startswith("http"):
            audio_url = fp
        elif fp:
            audio_url = f"/audio/{fp.replace('static/audio/', '')}"
        call_payload = {
            "call_id": int(r["call_id"]),
            "timestamp": int(r["start_epoch_s"]),
            "duration_s": float(r["duration_s"]),
            "talkgroup": str(r.get("talkgroup") or ""),
            "talkgroup_name": str(r.get("talkgroup_name") or ""),
            "system_id": int(r.get("radio_system_id")) if r.get("radio_system_id") is not None else None,
            "system_name": str(r.get("system_name") or ""),
            "transcript": (r.get("text_full") or "").strip(),
            "incident_category": str(r.get("incident_category") or "Other"),
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
            "address": address,
            "audio_url": audio_url,
            "has_location": lat is not None and lng is not None,
            "is_corrected": is_corrected,
            "correction_notes": str(r.get("correction_notes") or ""),
        }
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("PUBLIC_MAP_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = requests.post(
            public_map_url,
            json={"calls": [call_payload]},
            timeout=5,
            headers=headers,
        )
        if resp.status_code == 200:
            logger.info("Re-pushed corrected call_id=%s to public_map", call_id)
        else:
            logger.warning(
                "Re-push to public_map failed: call_id=%s status=%s body=%s",
                call_id, resp.status_code, resp.text[:200],
            )
    except Exception as e:
        logger.warning("Re-push to public_map failed: call_id=%s err=%s", call_id, e)


def _err(msg, code=400):
    return jsonify(success=False, message=msg, result={}), code
