# routes/api/trigger_calls.py
from collections import defaultdict
from flask import Blueprint, request, jsonify, current_app
from .call_upload import _build_audio_url      # we already wrote this helper

bp_trig = Blueprint("api_trigger_calls", __name__)


# ────────────────────────────────────────────────────────────────────────────
#  GET /api/trigger-calls
#     ?radio_system_id=1              (required)
#     &alert_trigger_id=3             (optional – return calls where THAT trigger fired)
#     &limit=100 &offset=0            (pagination)
#
#  Each row = one call                     ┌──────────────┐
#         {                                │  triggers    │   list of {id,name}
#           call_id: 12,                   └──────────────┘
#           start_epoch: 1722431142,
#           duration_s: 17.5,
#           talkgroup: "100",
#           audio_url: "https://…/static/audio/…mp3",
#           triggers: [ {id:3,name:"Structure"}, {id:7,name:"EMS"} ]
#         }
# ────────────────────────────────────────────────────────────────────────────
@bp_trig.route("", methods=["GET"])
def list_trigger_calls():
    db      = current_app.config["db"]
    logger  = current_app.config["logger"]

    # -------- query params --------------------------------------------------
    try:
        rsid = int(request.args["radio_system_id"])
    except (KeyError, ValueError):
        return _err("radio_system_id (int) is required", 400)

    trig_filter = request.args.get("alert_trigger_id")
    trig_filter_q = ""
    params = [rsid]

    if trig_filter:
        try:
            trig_id_int = int(trig_filter)
        except ValueError:
            return _err("alert_trigger_id must be int", 400)
        trig_filter_q = "AND tf.alert_trigger_id = ?"
        params.append(trig_id_int)

    limit  = max(1, min(int(request.args.get("limit", 100)), 500))
    offset = max(0, int(request.args.get("offset", 0)))
    params.extend([limit, offset])

    # -------- SQL (single pass) --------------------------------------------
    sql = f"""
        SELECT cr.call_id,
               cr.start_epoch_s,
               cr.duration_s,
               cr.talkgroup,
               cr.file_path,
               tf.alert_trigger_id,
               at.alert_trigger_name
        FROM   call_records   cr
        JOIN   trigger_fires  tf  USING(call_id)
        JOIN   alert_triggers at  USING(alert_trigger_id)
        WHERE  cr.radio_system_id = ?
              {trig_filter_q}
        ORDER  BY cr.start_epoch_s DESC
        LIMIT  ? OFFSET ?
    """
    res = db.execute_query(sql, params, fetch_mode="all")
    if not res["success"]:
        logger.error("trigger-calls list query failed: %s", res["message"])
        return _err("DB error", 500)

    # -------- munge rows into unique-call objects ---------------------------
    calls: dict[int, dict] = {}
    for r in res["result"]:
        if r["call_id"] not in calls:
            calls[r["call_id"]] = {
                "call_id"     : r["call_id"],
                "start_epoch" : r["start_epoch_s"],
                "duration_s"  : r["duration_s"],
                "talkgroup"   : r["talkgroup"],
                "audio_url"   : _build_audio_url(r["file_path"]),
                "triggers"    : []
            }
        calls[r["call_id"]]["triggers"].append({
            "id"  : r["alert_trigger_id"],
            "name": r["alert_trigger_name"]
        })

    return jsonify(success=True, result=list(calls.values()))


# Optional: re-expose the single-call detail we already have ---------------
# (Feel free to import bp_tone.one_call and re-route if you don't want dup.)
# --------------------------------------------------------------------------

def _err(msg, code=400):
    return jsonify(success=False, message=msg, result=[]), code
