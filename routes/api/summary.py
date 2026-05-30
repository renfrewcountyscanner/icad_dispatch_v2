# routes/api/summary.py
"""
REST endpoint for the Call Summary page.

GET /api/summary/calls
    ?radio_system_id=<int>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD

Returns a flat list of calls with full transcripts and address info
for a given radio system and date range.
"""
import json
import datetime
from flask import Blueprint, request, jsonify, current_app
from routes.decorators import login_required

bp_summary = Blueprint("api_summary", __name__)


def _parse_township(addr_extracted_json: str | None, addr_geocoded_json: str | None) -> str:
    """Try to extract a township/location label from address JSON."""
    # Prefer geocoded
    if addr_geocoded_json:
        try:
            geo = json.loads(addr_geocoded_json)
            parts = []
            for key in ("city", "township", "county"):
                val = geo.get(key)
                if val:
                    parts.append(str(val))
            if parts:
                return ", ".join(parts)
        except Exception:
            pass
    # Fallback to extracted
    if addr_extracted_json:
        try:
            ext = json.loads(addr_extracted_json)
            parts = []
            for key in ("city", "township", "county"):
                val = ext.get(key)
                if val:
                    parts.append(str(val))
            if parts:
                return ", ".join(parts)
        except Exception:
            pass
    return "—"


@bp_summary.route("/calls", methods=["GET"])
@login_required
def list_summary_calls():
    db = current_app.config["db"]
    logger = current_app.config["logger"]

    rsid_arg = request.args.get("radio_system_id")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    # Validate params
    if not rsid_arg:
        return _err("radio_system_id is required", 400)
    try:
        rsid = int(rsid_arg)
    except ValueError:
        return _err("invalid radio_system_id", 400)

    if not date_from or not date_to:
        return _err("date_from and date_to are required", 400)

    try:
        from_ts = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp()
        to_ts = datetime.datetime.strptime(date_to, "%Y-%m-%d").timestamp() + 86400
    except ValueError:
        return _err("invalid date format (expected YYYY-MM-DD)", 400)

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
               rs.system_name
        FROM   call_records cr
        LEFT   JOIN call_transcripts ct USING(call_id)
        LEFT   JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
        WHERE  cr.radio_system_id = ?
          AND  cr.start_epoch_s >= ?
          AND  cr.start_epoch_s < ?
        ORDER  BY cr.start_epoch_s DESC
    """

    res = db.execute_query(sql, (rsid, from_ts, to_ts), fetch_mode="all")
    if not res["success"]:
        logger.error("summary query failed: %s", res["message"])
        return _err("DB error", 500)

    rows = []
    for r in res["result"]:
        rows.append({
            "call_id": r["call_id"],
            "start_epoch": r["start_epoch_s"],
            "duration_s": r["duration_s"],
            "talkgroup": r.get("talkgroup") or "",
            "talkgroup_name": r.get("talkgroup_name") or "",
            "system_name": r.get("system_name") or "",
            "transcript": (r.get("text_full") or "").strip(),
            "township": _parse_township(
                r.get("address_extracted_json"),
                r.get("address_geocoded_json")
            ),
            "incident_category": r.get("incident_category") or "",
        })

    return jsonify(success=True, result=rows)


def _err(msg, code=400):
    return jsonify(success=False, message=msg, result=[]), code
