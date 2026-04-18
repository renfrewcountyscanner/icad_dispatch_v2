# routes/api/tone_finder.py
"""
REST endpoints that expose the “tone-finder” view.

Changes for **new schema** (2025-08):

* `call_tone_events.matches_trigger` tells if that tone-set fired any trigger.
* `tone_trigger_map` maps every tone-event that fired ⟷ alert_trigger_id(s).

New capabilities
────────────────
• GET /api/tone-finder/calls
      ?trigger_only=1   → list only calls where *any* tone fired a trigger
• JSON for each call now contains  `"has_trigger": true|false`
• GET /api/tone-finder/calls/<call_id>  returns:
      "tones": [ { …, "matches_trigger": 0/1, "trigger_ids":[…] }, … ]
"""
import json
import os
import datetime
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, request, jsonify, current_app, render_template
from ..decorators import login_required, csrf_protect

bp_tone = Blueprint("api_tone_finder", __name__)

ALLOWED_TONE_TYPES = {"two_tone", "long", "hi_low", "mdc", "dtmf", "pulsed"}

_AUDIO_DIR = Path("static/audio")
_AUDIO_DIR_SLUG = _AUDIO_DIR.as_posix().lstrip("/")

# ───────────────────────────────────────────────────────────────
#  GET /api/tone-finder/calls
# ───────────────────────────────────────────────────────────────
@bp_tone.route("/calls", methods=["GET"])
@login_required
def list_calls():
    db     = current_app.config["db"]
    logger = current_app.config["logger"]

    # ── params ──────────────────────────────
    rsid = None

    # Try "radio_system_id" first, then fall back to "system" (system_decimal)
    rsid_arg = request.args.get("radio_system_id")
    if rsid_arg:
        try:
            rsid = int(rsid_arg)
        except ValueError:
            pass

    # If not found, try "system" which is system_decimal and convert to radio_system_id
    if not rsid:
        system_arg = request.args.get("system")
        if system_arg:
            try:
                system_decimal = int(system_arg)
                sys_row = db.execute_query(
                    "SELECT radio_system_id FROM radio_systems WHERE system_decimal = ?",
                    (system_decimal,),
                    fetch_mode="one"
                )
                if sys_row.get("success") and sys_row.get("result"):
                    rsid = sys_row["result"]["radio_system_id"]
            except ValueError:
                pass

    tone_type = request.args.get("tone_type")
    if tone_type and tone_type not in ALLOWED_TONE_TYPES:
        return _err("invalid tone_type", 400)

    trigger_only = request.args.get("trigger_only") in ("1", "true", "yes")
    trigger_id = request.args.get("trigger_id")

    # Date filters
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    limit  = max(1, min(int(request.args.get("limit", 100)), 500))
    offset = max(0, int(request.args.get("offset", 0)))

    # ── SQL build ────────────────────────────────────────────────
    tone_filter  = []
    params       = []

    if rsid:
        tone_filter.append("cr.radio_system_id = ?")
        params.append(rsid)

    # Filter by specific trigger
    if trigger_id:
        tone_filter.append("""
            cr.call_id IN (
                SELECT DISTINCT ttm.call_id FROM tone_trigger_map ttm
                WHERE ttm.alert_trigger_id = ?
            )
        """)
        params.append(int(trigger_id))

    if tone_type:
        tone_filter.append("cte.tone_type = ?")
        params.append(tone_type)
    if trigger_only:
        tone_filter.append("cte.matches_trigger = 1")

    # Date filters
    if date_from:
        tone_filter.append("cr.start_epoch_s >= ?")
        params.append(datetime.strptime(date_from, "%Y-%m-%d").timestamp())
    if date_to:
        # Include the entire day
        tone_filter.append("cr.start_epoch_s < ?")
        params.append(datetime.strptime(date_to, "%Y-%m-%d").timestamp() + 86400)

    where_extra = f"WHERE {' AND '.join(tone_filter)}" if tone_filter else "WHERE 1=1"

    sql = f"""
        SELECT cr.call_id,
               cr.start_epoch_s,
               cr.duration_s,
               cr.talkgroup,
               cr.talkgroup_name,
               cr.file_path,
               cr.radio_system_id,
               rs.system_name,
               MAX(cte.matches_trigger) AS has_trigger,
               COUNT(cte.tone_event_id) AS tone_count,
               CASE
                   WHEN ct.call_id IS NULL THEN 0
                   ELSE 1
               END AS has_transcript,
               MAX(
                   CASE
                       WHEN ct.address_extracted_json IS NOT NULL
                            AND ct.address_extracted_json != '' THEN 1
                       ELSE 0
                   END
               ) AS has_address_extracted,
               MAX(
                   CASE
                       WHEN ct.address_geocoded_json IS NOT NULL
                            AND ct.address_geocoded_json != '' THEN 1
                       ELSE 0
                   END
               ) AS has_address_geocoded,
               MAX(ct.text_full)                AS transcript_text,
               MAX(ct.address_geocoded_json)    AS address_geocoded_json,
               MAX(ct.incident_category)        AS incident_category,
               GROUP_CONCAT(DISTINCT at.alert_trigger_name) AS fired_trigger_names

        FROM   call_records      cr
        JOIN   call_tone_events  cte USING(call_id)
        LEFT   JOIN call_transcripts ct USING(call_id)
        LEFT   JOIN radio_systems rs ON cr.radio_system_id = rs.radio_system_id
        LEFT   JOIN trigger_fires tf ON cr.call_id = tf.call_id
        LEFT   JOIN alert_triggers at ON tf.alert_trigger_id = at.alert_trigger_id
        {where_extra}
        GROUP  BY cr.call_id
        ORDER  BY cr.start_epoch_s DESC
        LIMIT  ? OFFSET ?
    """

    params.extend([limit, offset])

    res = db.execute_query(sql, params, fetch_mode="all")
    if not res["success"]:
        logger.error("tone-finder list query failed: %s", res["message"])
        return _err("DB error", 500)

    rows = []
    for r in res["result"]:
        # ----- simple transcript (short text) -----
        full_text = (r.get("transcript_text") or "").strip()
        transcript_simple = full_text[:200] if full_text else None  # tweak length as you like

        # ----- simple location (label + lat/lng) -----
        location_label = None
        location_lat   = None
        location_lng   = None

        geo_raw = r.get("address_geocoded_json")
        if geo_raw:
            try:
                geo = json.loads(geo_raw)
                location_label = (
                        geo.get("formatted_address")
                        or ", ".join(
                    part for part in [
                        geo.get("city"),
                        geo.get("county"),
                        geo.get("state")
                    ] if part
                ) or None
                )
                location_lat = geo.get("lat")
                location_lng = geo.get("lng")
            except Exception:
                # if JSON is bad, just leave location_* as None
                pass

        # ----- simple classification (just the category) -----
        incident_category = r.get("incident_category")

        fired_trigger_names = r.get("fired_trigger_names") or ""
        fired_triggers = [
            {"trigger_name": n.strip()}
            for n in fired_trigger_names.split(",")
            if n.strip()
        ]

        rows.append(
            {
                "call_id"      : r["call_id"],
                "start_epoch"  : r["start_epoch_s"],
                "duration_s"   : r["duration_s"],
                "talkgroup"    : r["talkgroup"],
                "talkgroup_name": r.get("talkgroup_name") or "",
                "audio_url"    : r["file_path"],
                "tone_count"   : r["tone_count"],
                "has_trigger"  : bool(r["has_trigger"]),
                "has_transcript": bool(r["has_transcript"]),
                "has_address_extracted": bool(r["has_address_extracted"]),
                "has_address_geocoded" : bool(r["has_address_geocoded"]),
                "system_name" : r.get("system_name") or "",
                "fired_triggers": fired_triggers,

                # new "simple" fields for the table
                "transcript_simple": transcript_simple,
                "location_label"   : location_label,
                "location_lat"     : location_lat,
                "location_lng"     : location_lng,
                "incident": incident_category,
            }
        )

    return jsonify(success=True, result=rows)


# ───────────────────────────────────────────────────────────────
#  GET /api/tone-finder/calls/<call_id>
# ───────────────────────────────────────────────────────────────
@bp_tone.route("/calls/<int:call_id>", methods=["GET"])
@login_required
def one_call(call_id: int):
    db     = current_app.config["db"]
    logger = current_app.config["logger"]

    with_words = request.args.get("with_words") in ("1", "true", "yes")

    # ── call row ─────────────────────────────────────────────────
    call_r = db.execute_query(
        "SELECT * FROM call_records WHERE call_id=?",
        (call_id,), fetch_mode="one"
    )
    if not call_r["success"] or not call_r["result"]:
        return _err("call_id not found", 404)
    c = call_r["result"]

    # ── tones   (annotated with trigger ids) ─────────────────────
    tone_q = """
             SELECT  cte.*,
                     GROUP_CONCAT(ttm.alert_trigger_id) AS trigger_ids
             FROM    call_tone_events           cte
                         LEFT JOIN tone_trigger_map ttm USING(tone_event_id)
             WHERE   cte.call_id = ?
             GROUP   BY cte.tone_event_id
             ORDER   BY cte.tone_type, cte.start_s \
             """
    tones_res = db.execute_query(tone_q, (call_id,), fetch_mode="all")
    tones_out = []
    if tones_res["success"]:
        for t in tones_res["result"]:
            # Decode once so UI doesn't need to parse JSON text
            try:
                payload = json.loads(t["json_payload"] or "{}")
            except Exception:
                payload = {}

            out = {
                "tone_event_id"  : t["tone_event_id"],
                "tone_type"      : t["tone_type"],
                "tone_set_id"    : t["tone_set_id"],
                "matches_trigger": bool(t["matches_trigger"]),
                "trigger_ids"    : [int(x) for x in t["trigger_ids"].split(",")] if t["trigger_ids"] else [],
                "freq_a"         : t["freq_a"],
                "freq_b"         : t["freq_b"],
                "length_a_s"     : t["length_a_s"],
                "length_b_s"     : t["length_b_s"],
                "start_s"        : t["start_s"],
                "end_s"          : t["end_s"],
                "payload"        : payload,
            }

            # Convenience fields by type (so the modal can show columns without parsing)
            if t["tone_type"] == "two_tone":
                out["tone_a_length_s"] = payload.get("tone_a_length")
                out["tone_b_length_s"] = payload.get("tone_b_length")
            elif t["tone_type"] == "hi_low":
                out["alternations"] = payload.get("alternations")
                out["interval_s"]   = payload.get("interval")
            elif t["tone_type"] == "pulsed":
                out["pulsed_cycles"] = payload.get("cycles")
                out["pulsed_on_ms"]  = payload.get("on_ms")
                out["pulsed_off_ms"] = payload.get("off_ms")

            tones_out.append(out)

    # ── triggers fired (legacy view, still useful) ───────────────
    fires = db.execute_query(
        """
        SELECT f.alert_trigger_id, f.fired_at_epoch_s, t.alert_trigger_name
        FROM   trigger_fires   f
                   JOIN   alert_triggers  t USING(alert_trigger_id)
        WHERE  f.call_id = ?
        ORDER  BY f.fired_at_epoch_s
        """,
        (call_id,), fetch_mode="all"
    )
    fires = fires["result"] if fires["success"] else []

    # ── VAD segments (optional new block) ────────────────────────
    vad_segments = _get_vad_segments(db, call_id)
    if vad_segments:
        voice_total   = sum(s["length_s"] for s in vad_segments if s["kind"] == "voice")
        silence_total = sum(s["length_s"] for s in vad_segments if s["kind"] == "silence")
        total         = voice_total + silence_total
        vad_block = {
            "segments": vad_segments,
            "voice_total_s": voice_total,
            "silence_total_s": silence_total,
            "voice_ratio": (voice_total / total) if total > 0 else 0.0,
        }
    else:
        vad_block = {"segments": [], "voice_total_s": 0.0, "silence_total_s": 0.0, "voice_ratio": 0.0}

    # ── transcript + derived metadata (address + incident) ───────
    tx = _get_transcript_block(db, call_id, with_words=with_words)
    header = tx["header"] or {}

    addr_extracted = header.get("address_extracted")
    addr_geocoded  = header.get("address_geocoded")
    incident_classification = header.get("incident_classification")

    # ── payload ──────────────────────────────────────────────────
    payload = {
        # Core call metadata (basically your call_records row)
        "call": {

            "call_id"         : c["call_id"],
            "radio_system_id" : c["radio_system_id"],
            "start_epoch"     : c["start_epoch_s"],
            "duration_s"      : c["duration_s"],
            "talkgroup"       : c["talkgroup"],
            "merged_from_stub": bool(c["merged_from_stub"]),
            "audio_url"       : c["file_path"],
        },

        # Where / location info
        "location": {
            "extracted": addr_extracted,   # may be None
            "geocoded" : addr_geocoded,    # may be None
        },

        # What / nature of incident
        "incident":  incident_classification,

        # Signal-level info
        "tones"   : tones_out,
        "triggers": fires,
        "vad"     : vad_block,

        # Full transcript block for detailed view / modal
        "transcript"          : tx["header"],
        "transcript_segments" : tx["segments"],
    }

    return jsonify(success=True, result=payload)

# ───────────────────────────────────────────────────────────────
#  DELETE /api/tone-finder/calls/<call_id>
# ───────────────────────────────────────────────────────────────
@bp_tone.route("/calls/<int:call_id>", methods=["DELETE"])
@login_required
def delete_call(call_id: int):
    db     = current_app.config["db"]
    logger = current_app.config["logger"]

    # ── optional CSRF snippet (unchanged) ───────────────────────
    if request.is_json:
        csrf = (request.json or {}).get("_csrf_token", "")
        if not csrf or csrf != request.cookies.get("_csrf_token"):
            return _err("CSRF token missing / invalid", 403)

    # ── locate file before deleting DB row ──────────────────────
    file_q = db.execute_query(
        "SELECT file_path FROM call_records WHERE call_id=?", (call_id,),
        fetch_mode="one"
    )
    if not file_q["success"]:
        return _err("DB error while locating call", 500)
    if not file_q["result"]:
        return _err("call_id not found", 404)

    file_path = file_q["result"]["file_path"]            # e.g. static/audio/…

    # ── remove local file (best-effort; only if under static/audio) ────────
    local_path = _resolve_local_audio_path(file_path)
    if local_path is not None:
        try:
            if local_path.exists():
                local_path.unlink()
                logger.info("Deleted local audio file for call_id=%s: %s", call_id, local_path)
            else:
                logger.debug("Local audio file not found for call_id=%s: %s", call_id, local_path)
        except Exception as e:
            logger.warning(
                "Could not delete local audio file for call_id=%s (%s): %s",
                call_id, local_path, e,
            )

    # ── delete DB row (cascades to child tables) ────────────────
    del_res = db.execute_commit(
        "DELETE FROM call_records WHERE call_id=?", (call_id,),
        return_row_id=False, return_count=True
    )

    if not del_res["success"]:
        logger.error("call delete failed id=%s: %s", call_id, del_res["message"])
        return _err("DB error while deleting", 500)

    return jsonify(success=True,
                   message=f"Call {call_id} deleted.",
                   result={})

@bp_tone.route("/calls/bulk-delete", methods=["POST"])
@csrf_protect
@login_required
def bulk_delete_calls():
    db     = current_app.config["db"]
    logger = current_app.config["logger"]

    if not request.is_json:
        return _err("JSON body required", 400)
    body = request.get_json(force=True) or {}
    ids  = body.get("call_ids") or []

    # Validate ids
    try:
        ids = [int(x) for x in ids]
    except Exception:
        return _err("call_ids must be integers", 400)
    ids = list(dict.fromkeys(ids))  # de-dup, preserve order
    if not ids:
        return _err("call_ids is empty", 400)

    # SQLite variable limit ~999 → chunk
    CHUNK = 200
    deleted_ids, not_found_ids, file_errors = [], [], []

    # We’ll select file paths first (per chunk), best-effort remove files, then delete rows.
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i+CHUNK]
        placeholders = ",".join("?" * len(chunk))

        q = f"SELECT call_id, file_path FROM call_records WHERE call_id IN ({placeholders})"
        res = db.execute_query(q, chunk, fetch_mode="all")
        if not res["success"]:
            logger.error("bulk delete: SELECT failed: %s", res["message"])
            return _err("DB error while locating calls", 500)

        found = {r["call_id"]: r["file_path"] for r in res["result"]}
        not_found_ids.extend([cid for cid in chunk if cid not in found])

        # delete local files (best-effort)
        for cid, file_path in found.items():
            try:
                local_path = _resolve_local_audio_path(file_path)
                if local_path is None:
                    # remote or not under static/audio → skip
                    logger.debug(
                        "Skipping remote/non-local audio for call_id=%s, file_path=%r",
                        cid, file_path,
                    )
                    continue

                if local_path.exists():
                    local_path.unlink()
                else:
                    logger.debug(
                        "Local audio file not found for call_id=%s: %s",
                        cid, local_path,
                    )
            except Exception as e:
                file_errors.append({"call_id": cid, "error": str(e)})

        # delete db rows (cascades)
        dq = f"DELETE FROM call_records WHERE call_id IN ({placeholders})"
        dres = db.execute_commit(dq, chunk, return_count=True)
        if not dres["success"]:
            logger.error("bulk delete: DELETE failed: %s", dres["message"])
            return _err("DB error while deleting", 500)

        # Count how many from this chunk actually deleted (intersection with found)
        deleted_ids.extend(list(found.keys()))

    return jsonify(success=True, result={
        "deleted_ids": deleted_ids,
        "not_found_ids": not_found_ids,
        "file_errors": file_errors,
        "deleted_count": len(deleted_ids),
    })

# ───────────────────────────────────────────────────────────────
#  POST /api/tone-finder/reprocess-triggers
# ───────────────────────────────────────────────────────────────
@bp_tone.route("/reprocess-triggers", methods=["POST"])
@csrf_protect
@login_required
def reprocess_triggers():
    """
    Re-evaluate all stored tone events against current triggers without
    re-running audio processing.  Useful after adding new triggers so that
    historical calls get properly flagged.
    """
    import json as _json
    import time as _time
    from collections import defaultdict
    from routes.api.call_upload import _load_triggers, _tones_matching_trigger

    db     = current_app.config["db"]
    logger = current_app.config["logger"]

    logger.info("reprocess-triggers: endpoint called")
    body   = request.get_json(force=True) or {} if request.is_json else {}
    rsid   = body.get("radio_system_id")
    logger.info("reprocess-triggers: radio_system_id=%s", rsid)
    if rsid:
        try:
            rsid = int(rsid)
        except (TypeError, ValueError):
            rsid = None

    # ── fetch all calls that have tone events ───────────────────
    where  = "WHERE cr.radio_system_id = ?" if rsid else ""
    params = [rsid] if rsid else []
    calls_res = db.execute_query(
        f"""
        SELECT DISTINCT cr.call_id, cr.radio_system_id, cr.talkgroup, cr.start_epoch_s
        FROM   call_records cr
        JOIN   call_tone_events cte USING(call_id)
        {where}
        ORDER  BY cr.call_id
        """,
        params, fetch_mode="all"
    )
    if not calls_res["success"]:
        return _err("DB error fetching calls", 500)

    updated = 0
    errors  = 0
    now_ts  = int(_time.time())

    for call in (calls_res["result"] or []):
        cid = call["call_id"]
        sid = call["radio_system_id"]
        tg  = str(call["talkgroup"] or "")

        try:
            # ── fetch & reconstruct stored tone events ──────────
            tones_res = db.execute_query(
                "SELECT tone_type, json_payload FROM call_tone_events WHERE call_id = ?",
                (cid,), fetch_mode="all"
            )
            if not tones_res["success"]:
                errors += 1
                continue

            groups = defaultdict(list)
            for t in (tones_res["result"] or []):
                try:
                    payload = _json.loads(t.get("json_payload") or "{}")
                except Exception:
                    payload = {}
                groups[t["tone_type"]].append(payload)

            class _Det:
                two_tone_result = groups.get("two_tone", [])
                long_result     = groups.get("long", [])
                hi_low_result   = groups.get("hi_low", [])
                pulsed_result   = groups.get("pulsed", [])
                dtmf_result     = groups.get("dtmf", [])

            det = _Det()

            # ── match against current triggers ──────────────────
            triggers   = _load_triggers(db, sid)
            fired_ids  = []
            for trig in triggers:
                tg_pat = str(trig.get("alert_trigger_talkgroup") or "")
                if tg_pat and tg_pat != tg:
                    continue
                hits = _tones_matching_trigger(trig, det)
                if hits:
                    fired_ids.append(trig["alert_trigger_id"])

            # ── update trigger_fires ────────────────────────────
            db.execute_commit("DELETE FROM trigger_fires WHERE call_id = ?", (cid,))
            for tid in fired_ids:
                db.execute_commit(
                    """
                    INSERT INTO trigger_fires (call_id, alert_trigger_id, fired_at_epoch_s)
                    VALUES (?, ?, ?)
                    """,
                    (cid, tid, now_ts)
                )

            # ── update matches_trigger flag on tone events ──────
            db.execute_commit(
                "UPDATE call_tone_events SET matches_trigger = ? WHERE call_id = ?",
                (1 if fired_ids else 0, cid)
            )

            updated += 1

        except Exception as exc:
            logger.warning("reprocess-triggers: error on call_id=%s: %s", cid, exc)
            errors += 1

    logger.info("reprocess-triggers complete: updated=%d errors=%d", updated, errors)
    return jsonify(success=True, result={"updated": updated, "errors": errors})


# ───────────────────────────────────────────────────────────────
#  helper
# ───────────────────────────────────────────────────────────────
def _err(msg, code=400):
    return jsonify(success=False, message=msg, result=[]), code

def _get_vad_segments(db, call_id: int):
    """
    Return VAD segments for a call as a list of dicts:
        [{ "kind": "voice"|"silence", "start_s": float, "end_s": float, "length_s": float }, ...]
    If none exist, returns [].
    """
    q = """
        SELECT kind, start_s, end_s
        FROM   call_vad_segments
        WHERE  call_id=?
        ORDER  BY start_s \
        """
    res = db.execute_query(q, (call_id,), fetch_mode="all")
    if not res["success"]:
        return []
    out = []
    for r in res["result"]:
        s = float(r["start_s"] or 0.0)
        e = float(r["end_s"] or s)
        out.append({
            "kind": r["kind"],
            "start_s": s,
            "end_s": e,
            "length_s": max(0.0, e - s),
        })
    return out

def _get_transcript_block(db, call_id: int, with_words: bool = False):
    """
    Return a dict:
        {
          "header": {
              "model": ...,
              "language": ...,
              "duration_s": ...,
              "text_full": ...,
              "has_words": 0/1,

              "address_extracted_json": TEXT|NULL,
              "address_geocoded_json":  TEXT|NULL,
              "address_extracted": {...} | None,   # parsed JSON
              "address_geocoded":  {...} | None,   # parsed JSON

              "incident_category": TEXT|NULL,                  # simple category string
              "incident_classification_json": TEXT|NULL,       # raw JSON blob
              "incident_classification": {...} | None,         # parsed JSON (IncidentClassification)
          } | None,
          "segments": [ {seg...}, ... ]
        }
    """
    hdr_q = """
            SELECT
                model,
                language,
                duration_s,
                text_full,
                has_words,
                address_extracted_json,
                address_geocoded_json,
                incident_category,
                incident_classification_json
            FROM   call_transcripts
            WHERE  call_id=?
            LIMIT  1 \
            """
    seg_q = """
            SELECT seg_index, start_s, end_s, text, word_count, words_json
            FROM   call_transcript_segments
            WHERE  call_id=?
            ORDER  BY start_s, seg_index \
            """
    hdr = db.execute_query(hdr_q, (call_id,), fetch_mode="one")
    if not hdr["success"] or not hdr["result"]:
        return {"header": None, "segments": []}

    row = hdr["result"]

    # Parse the JSON address blobs into nicer objects
    addr_extracted = None
    addr_geocoded  = None

    try:
        if row.get("address_extracted_json"):
            addr_extracted = json.loads(row["address_extracted_json"])
    except Exception:
        addr_extracted = None

    try:
        if row.get("address_geocoded_json"):
            addr_geocoded = json.loads(row["address_geocoded_json"])
    except Exception:
        addr_geocoded = None

    # Parse incident classification JSON
    incident_classification = None
    try:
        if row.get("incident_classification_json"):
            incident_classification = json.loads(row["incident_classification_json"])
    except Exception:
        incident_classification = None

    header = dict(row)  # copy so we can safely mutate

    # Attach parsed helpers
    header["address_extracted"]       = addr_extracted
    header["address_geocoded"]        = addr_geocoded
    header["incident_category"]       = row.get("incident_category")
    header["incident_classification"] = incident_classification

    # Load segments
    segs_res = db.execute_query(seg_q, (call_id,), fetch_mode="all")
    segments = []
    if segs_res["success"]:
        for r in segs_res["result"]:
            item = {
                "seg_index": r["seg_index"],
                "start_s": float(r["start_s"] or 0.0),
                "end_s": float(r["end_s"] or 0.0),
                "text": (r["text"] or "").strip(),
                "word_count": int(r["word_count"] or 0),
            }
            if with_words and r.get("words_json"):
                try:
                    item["words"] = json.loads(r["words_json"])
                except Exception:
                    item["words"] = []
            segments.append(item)

    return {"header": header, "segments": segments}

def _resolve_local_audio_path(file_path: str | None) -> Path | None:
    """
    Given the stored file_path from call_records, return a Path to the local
    audio file *if* it lives under static/audio; otherwise return None.

    Handles:
      - "static/audio/2025/12/..."
      - "/static/audio/2025/12/..."
      - "https://codeholio.icaddispatch.com/static/audio/2025/12/..."
      - "codeholio.icaddispatch.com/static/audio/2025/12/..."
    """
    if not file_path:
        return None

    file_path = file_path.strip()
    if not file_path:
        return None

    # If it's a URL with scheme/netloc, use its path; otherwise treat as raw path
    parsed = urlparse(file_path)
    if parsed.scheme and parsed.netloc:
        path_part = parsed.path or ""
    else:
        path_part = file_path

    # We only consider it local if it contains our audio dir
    idx = path_part.find(_AUDIO_DIR_SLUG)
    if idx == -1:
        # e.g. https://audio.bcfirewire.com/detect/... or s3://...
        return None

    # Start from "static/audio/..."
    rel = path_part[idx:].lstrip("/")  # "static/audio/2025/12/..."
    return Path(current_app.root_path) / rel
