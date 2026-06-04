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
        ORDER  BY cr.start_epoch_s DESC
        LIMIT  500
    """
    res = db.execute_query(sql, (since,), fetch_mode="all")
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

    return jsonify(success=True, result={"deleted": res.get("count", 0)})


def _err(msg, code=400):
    return jsonify(success=False, message=msg, result={}), code
