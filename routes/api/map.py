# routes/api/map.py
# CAD Map API - returns call data for mapping

from flask import Blueprint, request, jsonify, current_app
from routes.decorators import login_required

bp_map_api = Blueprint("map_api", __name__)


def _parse_timestamp(ts):
    """Convert Unix timestamp to readable time string."""
    from datetime import datetime
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return ""


def _parse_date(date_str):
    """Convert YYYY-MM-DD to start/end epoch timestamps."""
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start = int(dt.replace(tzinfo=timezone.utc).timestamp())
        end = start + 86400  # 24 hours
        return start, end
    except Exception:
        return None, None


@bp_map_api.route("/calls", methods=["GET"])
@login_required
def get_map_calls():
    """
    Get calls for map display.

    Query params:
        - start_date: YYYY-MM-DD (required)
        - end_date: YYYY-MM-DD (optional, defaults to start_date)
        - category: comma-separated list (fire,ems,other) - optional

    Returns list of calls with geocoded location data.
    """
    db = current_app.config["db"]

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date", start_date)
    category_filter = request.args.get("category", "").lower()

    if not start_date:
        return jsonify(success=False, message="start_date required (YYYY-MM-DD)"), 400

    start_ts, end_ts = _parse_date(start_date)
    if start_ts is None:
        return jsonify(success=False, message="Invalid start_date format"), 400

    if end_date:
        _, end_ts = _parse_date(end_date)

    if end_ts is None:
        end_ts = start_ts + 86400

    # Build category filter
    categories = None
    if category_filter:
        categories = [c.strip() for c in category_filter.split(",") if c.strip()]

    # Query calls with geocoded data
    query = """
        SELECT
            cr.call_id,
            cr.start_epoch_s,
            cr.duration_s,
            cr.talkgroup,
            cr.talkgroup_name,
            cr.incident_category,
            cr.address_geocoded_json,
            rs.system_name
        FROM call_records cr
        JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
        WHERE cr.start_epoch_s >= ? AND cr.start_epoch_s < ?
    """

    params = [start_ts, end_ts]

    if categories:
        placeholders = ",".join("?" * len(categories))
        query += f" AND cr.incident_category IN ({placeholders})"
        params.extend(categories)

    query += " ORDER BY cr.start_epoch_s DESC"

    res = db.execute_query(query, tuple(params), fetch_mode="all")

    if not res.get("success"):
        return jsonify(success=False, message=res.get("message")), 500

    calls = []
    import json

    for row in (res["result"] or []):
        geo = None
        geo_raw = row.get("address_geocoded_json")
        if geo_raw:
            try:
                geo = json.loads(geo_raw)
            except Exception:
                pass

        # Skip calls without coordinates
        if not geo or not geo.get("lat") or not geo.get("lng"):
            continue

        calls.append({
            "call_id": row["call_id"],
            "time": _parse_timestamp(row["start_epoch_s"]),
            "epoch": row["start_epoch_s"],
            "category": row.get("incident_category") or "Other",
            "address": geo.get("formatted_address") if geo else None,
            "lat": geo.get("lat") if geo else None,
            "lng": geo.get("lng") if geo else None,
            "system": row.get("system_name"),
            "talkgroup": row.get("talkgroup_name") or str(row.get("talkgroup", "")),
            "duration": row.get("duration_s", 0),
        })

    return jsonify(success=True, result=calls), 200