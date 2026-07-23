# api_systems.py
from __future__ import annotations

import os
from pathlib import Path
import json
import math
import datetime
import time
import smtplib
import ssl
from email.message import EmailMessage

from flask import Blueprint, request, jsonify, abort, current_app, session

# ────────────────────────────────────────────────────────────────────
# Library modules
# ────────────────────────────────────────────────────────────────────
from lib.alert_trigger_module import (
    get_triggers, get_triggers_full,
    add_trigger, patch_trigger, delete_trigger,
    get_pushover_settings, patch_pushover,
)
from lib.discord_module import DiscordSender
from lib.dispatch_text_render import build_context
from lib.make_module import MakeSender
from lib.n8n_module import N8nSender
from lib.ntfy_module import NtfySender
from lib.system_module import (
    get_systems, add_system, delete_system, update_system_discord_settings,
    add_or_update_system_discord_field, delete_system_discord_field,
    reorder_system_discord_fields, add_or_update_system_make_field,
    delete_system_make_field, update_system_make_settings,
    update_system_telegram_settings, update_system_tone_settings,
    update_system_general, update_system_pushover_settings,
    add_or_update_system_email, delete_system_email, update_system_email_settings,
    update_system_transcribe_settings, update_system_upload_settings,
    get_system_address_extraction_settings,
    update_system_address_extraction_settings,
    add_or_update_geocoding_region,
    delete_geocoding_region,
    reorder_geocoding_regions,
    bulk_add_geocoding_regions,
    get_geocoding_cities,
    add_or_update_geocoding_city,
    delete_geocoding_city,
    reorder_geocoding_cities,
    bulk_add_geocoding_cities,
    get_geocoding_roads,
    add_or_update_geocoding_road,
    delete_geocoding_road,
    reorder_geocoding_roads,
    bulk_add_geocoding_roads,
    get_system_storage_settings, update_system_storage_settings,
    get_system_incident_classification_settings, update_system_incident_classification_settings,
    ensure_system_incident_classification_settings, get_system_n8n_settings, update_system_n8n_settings,
    update_system_ntfy_settings,
)
from lib.utility import _parse_int_or_list, _parse_str_or_list, _generate_api_key, _norm_str, alert_test_payload, \
    alert_test_fired_trigger, alert_test_transcribe
from routes.decorators import login_required, csrf_protect, permission_required


try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None

try:
    import jwt as pyjwt  # PyJWT
except Exception:  # pragma: no cover
    pyjwt = None

SSH_KEY_ROOT = Path("var/.ssh")

api_systems = Blueprint("api_systems", __name__)

# ======================================================================
# Helpers shared by trigger routes
# ======================================================================

# Child-set keys exposed in the new shape
_CHILD_KEYS = {"two_tone_sets", "long_tone_sets", "hi_low_sets", "pulsed_sets", "dtmf_sequences"}

def _coerce_child_arrays(d: dict) -> dict:
    """
    If a request sends any child-set arrays as JSON strings (common with form posts),
    parse them into Python lists. Unknown keys are ignored; non-list parses are ignored.

    Example:
      d = {"two_tone_sets": "[{...},{...}]"}  →  {"two_tone_sets": [{...},{...}]}
    """
    if not d:
        return d
    for k in _CHILD_KEYS:
        if k in d and isinstance(d[k], str):
            try:
                parsed = json.loads(d[k])
                if isinstance(parsed, list):
                    d[k] = parsed
            except Exception:
                # leave as-is; alert_trigger_module will ignore non-lists
                pass
    return d


# Metadata keys permitted on the main alert_triggers row (we intentionally
# do NOT accept legacy tone columns here — those have been removed).
_META_FLOATS = {"alert_trigger_tone_tolerance", "alert_trigger_ignore_time"}
_META_INTS = {
    "alert_trigger_talkgroup",
    "alert_trigger_enable_discord",
    "alert_trigger_enable_make",
    "alert_trigger_enable_telegram",
    "alert_trigger_enable_n8n",
    "alert_trigger_enabled",
}
_META_STRINGS = {"alert_trigger_name", "alert_trigger_type", "alert_trigger_stream_url"}

def _normalize_trigger_metadata(d: dict) -> dict:
    """
    Keep only allowed metadata keys and coerce types.
    - alert_trigger_type: normalized to "AND" or "OR" (default "AND" if unknown)
    - *_tolerance / *_ignore_time: float (or None)
    - *_talkgroup / enable_* / enabled: int (or None)
    """
    out = {}

    # Strings (name/type/stream)
    for k in _META_STRINGS:
        if k in d:
            v = d[k]
            if k == "alert_trigger_type" and isinstance(v, str):
                typ = v.strip().upper()
                out[k] = "OR" if typ == "OR" else "AND"
            else:
                out[k] = v

    # Floats
    for k in _META_FLOATS:
        if k in d and d[k] not in (None, ""):
            try:
                out[k] = float(d[k])
            except Exception:
                out[k] = None

    # Ints
    for k in _META_INTS:
        if k in d and d[k] not in (None, ""):
            try:
                out[k] = int(d[k])
            except Exception:
                out[k] = None

    return out

def _num_f(x):
    """tolerant float parse; returns None for '', None, NaN, bad values"""
    if x in (None, ""): return None
    try:
        v = float(x)
        return None if (isinstance(v, float) and math.isnan(v)) else v
    except Exception:
        return None

def _num_i(x):
    """tolerant int parse; returns None for '', None, bad values"""
    if x in (None, ""): return None
    try:
        return int(x)
    except Exception:
        return None

def _canonicalize_rule(type_key: str, rule: dict) -> dict:
    """
    Accept either canonical field names (freq_a_hz, min_len_a_s, ...)
    or friendly aliases (a/b/a_len/b_len/tol; f/min_len; etc.) and
    return a dict in the canonical server shape.

    This function is used only by the POST /rules (append one) endpoint.
    Bulk PATCH from the editor already uses canonical names.
    """
    r = dict(rule or {})

    if type_key == "two_tone_sets":
        # aliases → canonical
        mapping = {
            "a": "freq_a_hz",
            "b": "freq_b_hz",
            "a_len": "min_len_a_s",
            "b_len": "min_len_b_s",
            "tol": "tol_pct",
        }
        for src, dst in mapping.items():
            if src in r and dst not in r:
                r[dst] = r[src]
        # numeric normalization
        for k in ("freq_a_hz","min_len_a_s","freq_b_hz","min_len_b_s","tol_pct"):
            if k in r: r[k] = _num_f(r[k])

    elif type_key == "long_tone_sets":
        mapping = {"f":"freq_hz", "min_len":"min_len_s", "tol":"tol_pct"}
        for src, dst in mapping.items():
            if src in r and dst not in r:
                r[dst] = r[src]
        for k in ("freq_hz","min_len_s","tol_pct"):
            if k in r: r[k] = _num_f(r[k])

    elif type_key == "hi_low_sets":
        mapping = {
            "a":"hi_freq_a_hz", "b":"hi_freq_b_hz",
            "alternations":"min_alternations", "interval":"interval_s",
            "tol":"tol_pct",
        }
        for src, dst in mapping.items():
            if src in r and dst not in r:
                r[dst] = r[src]
        for k in ("hi_freq_a_hz","hi_freq_b_hz","interval_s","tol_pct"):
            if k in r: r[k] = _num_f(r[k])
        if "min_alternations" in r: r["min_alternations"] = _num_i(r["min_alternations"])

    elif type_key == "pulsed_sets":
        # allow friendly 'f', 'on_ms', 'off_ms' → canonical
        if "f" in r and "center_hz" not in r: r["center_hz"] = r["f"]
        for k in ("center_hz","tol_pct"):
            if k in r: r[k] = _num_f(r[k])

        # ranges or single values are both accepted
        singles_to_ranges = {
            "on_ms": ("min_on_ms", "max_on_ms"),
            "off_ms": ("min_off_ms", "max_off_ms"),
        }
        for single, (mn, mx) in singles_to_ranges.items():
            if single in r and mn not in r and mx not in r:
                r[mn] = r[single]
                r[mx] = r[single]

        for k in ("min_cycles","min_on_ms","max_on_ms","min_off_ms","max_off_ms"):
            if k in r: r[k] = _num_i(r[k])

    elif type_key == "dtmf_sequences":
        # allow 'digit' or 'digits' → sequence
        if "sequence" not in r:
            if "digits" in r: r["sequence"] = r["digits"]
            elif "digit" in r: r["sequence"] = r["digit"]
        if "sequence" in r and isinstance(r["sequence"], str):
            r["sequence"] = r["sequence"].strip().upper()

        if "match_type" in r and isinstance(r["match_type"], str):
            mt = r["match_type"].strip().upper()
            r["match_type"] = mt if mt in ("EXACT","PREFIX","CONTAINS") else "EXACT"
        if "max_gap_ms" in r:
            r["max_gap_ms"] = _num_i(r["max_gap_ms"])

    # pass through rule_uid / sort_order if supplied
    return r

def _fetch_system_with_config(db, radio_system_id: int):
    """
    Convenience helper: get a single radio_system row with include_config=True.
    Returns (ok, message, system_dict_or_None).
    """
    resp = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    if not resp.get("success"):
        return False, resp.get("message", "DB error fetching system."), None
    if not resp.get("result"):
        return False, "System not found.", None
    return True, "OK", resp["result"][0]

# ======================================================================
# /api/systems                     GET (list) | POST (create)
# ======================================================================
@api_systems.route("", methods=["GET", "POST"], strict_slashes=False)
@login_required
@csrf_protect
@permission_required('read', 'write')
def systems_collection():
    """
    Collection endpoint for radio systems.

    GET
    ---
    Query params:
      - radio_system_id: int | "1,2,3"
      - system_decimal : int | "100,200"
      - system_name    : str | "Alpha,Beta"
      - include_config : 0/1, true/false

    Returns JSON:
      {success, message, result}

    POST
    ----
    Body (JSON or form):
      { "system_decimal": int, "system_name": str, "stream_url": str|None }

    Returns JSON with 201 on success.
    """
    db = current_app.config["db"]

    # ---------- GET ----------------------------------------------------------
    if request.method == "GET":
        radio_system_id = _parse_int_or_list(request.args.get("radio_system_id"))
        system_decimal  = _parse_int_or_list(request.args.get("system_decimal"))
        system_name     = _parse_str_or_list(request.args.get("system_name"))
        include_config  = (request.args.get("include_config", "0").lower()
                           in ("1", "true", "yes"))

        # Get user_id from session for filtering
        user_id = session.get("user_id")

        resp = get_systems(
            db,
            radio_system_id=radio_system_id,
            system_decimal=system_decimal,
            system_name=system_name,
            include_config=include_config,
            user_id=user_id,
        )
        return jsonify(resp), (200 if resp.get("success") else 400)

    # ---------- POST ---------------------------------------------------------
    system_data = request.get_json(silent=True) if request.is_json else request.form.to_dict()

    # normalize numerics
    for k in ("system_decimal", "radio_system_id", "copy_from_radio_system_id"):
        if k in system_data and system_data[k] not in (None, ""):
            try:
                system_data[k] = int(system_data[k])
            except ValueError:
                pass

    resp = add_system(db, system_data)
    if not resp.get("success"):
        abort(400, resp.get("message", "Unable to create system"))

    return jsonify({
        "success": True,
        "message": f"System '{system_data.get('system_name','')}' added successfully.",
        "result": resp.get("result", [])
    }), 201

# ======================================================================
# /api/systems/<id>                GET (one) | PATCH | DELETE
# ======================================================================
@api_systems.route("/<int:radio_system_id>", methods=["GET", "PATCH", "DELETE"])
@login_required
@csrf_protect
@permission_required('read', 'write')
def systems_item(radio_system_id: int):
    """
    Item endpoint for a single radio system.

    GET    → returns one system (no config)
    PATCH  → updates "general" fields (requires write permission)
    DELETE → deletes the system (requires write permission)
    """
    db = current_app.config["db"]

    # ---------- GET ----------------------------------------------------------
    if request.method == "GET":
        user_id = session.get("user_id")
        res = get_systems(db, radio_system_id=radio_system_id, user_id=user_id)
        ok = bool(res.get("success") and res["result"])
        return jsonify(
            success=ok,
            message=res.get("message", "Not found" if not ok else "OK"),
            result=res["result"][0] if ok else []
        ), (200 if ok else 404)

    # ---------- DELETE -------------------------------------------------------
    if request.method == "DELETE":
        resp = delete_system(db, radio_system_id=radio_system_id)
        status = 200 if resp["success"] else 400
        return jsonify(
            success=resp["success"],
            message=resp.get("message", "System deleted."),
            result=[]
        ), status

    # ---------- PATCH --------------------------------------------------------
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id  # enforce URL

    upd = update_system_general(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    # refetch fresh copy
    refetch = get_systems(db, radio_system_id=radio_system_id)
    return jsonify(success=True, message="System updated.", result=refetch["result"][0]), 200


# ======================================================================
# /api/systems/<id>/apikey         POST (regenerate key)
# ======================================================================
@api_systems.route("/<int:radio_system_id>/apikey", methods=["POST"])
@login_required
def regenerate_api_key(radio_system_id: int):
    """
    Regenerate the API key for a system.

    POST body: { "_csrf_token": "…" }
    """
    db = current_app.config["db"]

    chk = db.execute_query(
        "SELECT 1 FROM radio_systems WHERE radio_system_id = ?",
        (radio_system_id,), fetch_mode="one"
    )
    if not chk["success"]:
        return jsonify(success=False, message=chk["message"], result=[]), 400
    if not chk["result"]:
        return jsonify(success=False, message="System not found.", result=[]), 404

    new_key = _generate_api_key()
    upd = db.execute_commit(
        "UPDATE radio_systems SET api_key = ? WHERE radio_system_id = ?",
        (new_key, radio_system_id),
        return_row_id=False
    )
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    return jsonify(success=True, message="API key regenerated.", result={"api_key": new_key}), 200


# ======================================================================
# Tone settings                     GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/tone/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_tone_settings(radio_system_id: int):
    """
    Get or update tone settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = get_systems(db, radio_system_id=radio_system_id, include_config=True)
        ok = bool(res.get("success") and res["result"])
        tone = res["result"][0]["tone"] if ok else {}
        return jsonify(success=ok,
                       message=res.get("message", "Not found" if not ok else "OK"),
                       result=tone), (200 if ok else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_tone_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    refetch = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    tone = refetch["result"][0]["tone"] if refetch["success"] else {}
    return jsonify(success=True, message="Tone settings updated.", result=tone), 200


# ---- Compatibility alias for edit_triggers.js probe -----------------
@api_systems.route("/<int:radio_system_id>/tone_settings", methods=["GET"], strict_slashes=False)
@login_required
@csrf_protect
def systems_tone_settings_alias(radio_system_id: int):
    """
    Compatibility alias for GET /tone/settings.
    edit_triggers.js first probes /tone_settings; serve the same payload.
    """
    return systems_tone_settings(radio_system_id)


# ======================================================================
# Telegram settings                 GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/telegram/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_telegram_settings(radio_system_id: int):
    """
    Get or update Telegram settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = get_systems(db, radio_system_id=radio_system_id, include_config=True)
        ok = bool(res.get("success") and res["result"])
        telegram = res["result"][0]["telegram"] if ok else {}
        return jsonify(success=ok,
                       message=res.get("message", "Not found" if not ok else "OK"),
                       result=telegram), (200 if ok else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_telegram_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    refetch = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    telegram = refetch["result"][0]["telegram"] if refetch["success"] else {}
    return jsonify(success=True, message="Telegram settings updated.", result=telegram), 200

@api_systems.route("/<int:radio_system_id>/telegram/test", methods=["POST"])
@login_required
@csrf_protect
def systems_telegram_test(radio_system_id: int):
    """
    Fire a simple test message to the configured Telegram channel.
    """
    db = current_app.config["db"]

    if requests is None:
        return jsonify(success=False, message="Python 'requests' package is not available on the server.", result=[]), 500

    ok, msg, system = _fetch_system_with_config(db, radio_system_id)
    if not ok:
        return jsonify(success=False, message=msg, result=[]), 404

    tg = (system.get("telegram") or {})
    enabled = int(tg.get("enabled") or 0)
    token   = tg.get("bot_token") or tg.get("botToken")
    chat_id = tg.get("channel_id")
    text_tpl = tg.get("message_body") or "Test alert from iCAD Dispatch."

    if not token or not chat_id:
        return jsonify(success=False, message="Telegram bot token and channel ID must be configured.", result=[]), 400

    text = f"[TEST] {system.get('system_name', 'Unknown system')} (ID {radio_system_id})\n\n{text_tpl}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok", True):
            return jsonify(success=False, message=f"Telegram API error: {data.get('description','Unknown error')}", result=data), 400
    except Exception as exc:
        return jsonify(success=False, message=f"Error calling Telegram API: {exc}", result=[]), 502

    return jsonify(success=True, message=f"Telegram test message sent to chat {chat_id}.", result=[]), 200

# =============================================================================
# Discord helpers
# =============================================================================
def _fetch_discord_settings_obj(db, radio_system_id: int):
    """
    Load the Discord settings object (including fields) via get_systems(include_config=True).
    """
    resp = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    if not resp.get("success") or not resp["result"]:
        return {"success": False, "message": resp.get("message", "System not found."), "result": {}}

    discord_obj = resp["result"][0].get("discord", {}) or {}
    return {"success": True, "message": "Discord settings retrieved.", "result": discord_obj}


def _fetch_discord_field_by_id(db, embed_field_id: int):
    """
    Fetch a single Discord embed field by its primary key.
    """
    q = """
        SELECT f.embed_field_id, f.discord_setting_id, f.field_key, f.field_label,
               f.field_template, f.field_inline, f.field_enabled, f.sort_order,
               s.radio_system_id
        FROM radio_system_discord_embed_fields AS f
                 JOIN radio_system_discord_settings AS s ON f.discord_setting_id = s.discord_setting_id
        WHERE f.embed_field_id = ? \
        """
    res = db.execute_query(q, (embed_field_id,), fetch_mode="one")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": {}}
    row = res.get("result")
    if not row:
        return {"success": False, "message": "Embed field not found.", "result": {}}
    return {"success": True, "message": "Discord field retrieved.", "result": row}


def _fetch_discord_fields_for_system(db, radio_system_id: int):
    """
    Fetch all Discord embed fields for a system.
    """
    q = """
        SELECT f.embed_field_id, f.discord_setting_id, f.field_key, f.field_label,
               f.field_template, f.field_inline, f.field_enabled, f.sort_order
        FROM radio_system_discord_embed_fields AS f
                 JOIN radio_system_discord_settings AS s ON f.discord_setting_id = s.discord_setting_id
        WHERE s.radio_system_id = ?
        ORDER BY f.sort_order ASC, f.embed_field_id ASC \
        """
    res = db.execute_query(q, (radio_system_id,), fetch_mode="all")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}
    return {"success": True, "message": "Discord fields retrieved.", "result": res.get("result", [])}


# ======================================================================
# Discord settings                   GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/discord/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_discord_settings(radio_system_id: int):
    """
    Get or update Discord settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_discord_settings_obj(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_discord_settings(db, payload)
    if not upd.get("success"):
        return jsonify({"success": False, "message": upd.get("message", "Failed to update Discord settings."), "result": []}), 400

    res = _fetch_discord_settings_obj(db, radio_system_id)
    res["message"] = "Discord settings updated." if res["success"] else "Discord settings updated, but re-fetch failed."
    return jsonify(res), 200


# ======================================================================
# Discord fields collection          GET | POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/discord/fields", methods=["GET", "POST"])
@login_required
@csrf_protect
def systems_discord_fields_collection(radio_system_id: int):
    """
    List or create Discord embed fields for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_discord_fields_for_system(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 400)

    field_data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    field_data["radio_system_id"] = radio_system_id

    ins = add_or_update_system_discord_field(db, field_data)
    if not ins.get("success"):
        return jsonify({"success": False, "message": ins.get("message", "Failed to add Discord field."), "result": []}), 400

    new_id = ins.get("result")
    fetched = _fetch_discord_field_by_id(db, new_id) if new_id else {"success": True, "result": {}}
    return jsonify({
        "success": True,
        "message": "Discord field added.",
        "result": fetched["result"] if fetched.get("success") else {"embed_field_id": new_id}
    }), 201


# ======================================================================
# Discord field item                 PATCH | DELETE
# ======================================================================
@api_systems.route("/<int:radio_system_id>/discord/fields/<int:embed_field_id>", methods=["PATCH", "DELETE"])
@login_required
@csrf_protect
def systems_discord_field_item(radio_system_id: int, embed_field_id: int):
    """
    Update or delete a single Discord embed field.
    """
    db = current_app.config["db"]

    # Ownership / existence check
    own_q = """
            SELECT f.embed_field_id
            FROM radio_system_discord_embed_fields AS f
                     JOIN radio_system_discord_settings AS s ON f.discord_setting_id = s.discord_setting_id
            WHERE f.embed_field_id = ? AND s.radio_system_id = ? \
            """
    own_res = db.execute_query(own_q, (embed_field_id, radio_system_id), fetch_mode="one")
    if not own_res.get("success"):
        return jsonify({"success": False, "message": own_res.get("message", "DB error validating field."), "result": []}), 400
    if not own_res.get("result"):
        return jsonify({"success": False, "message": f"Embed field {embed_field_id} not found for system {radio_system_id}.", "result": []}), 404

    if request.method == "DELETE":
        del_res = delete_system_discord_field(db, embed_field_id=embed_field_id)
        return jsonify({
            "success": del_res.get("success", False),
            "message": del_res.get("message", "Failed to delete Discord field."),
            "result": []
        }), (200 if del_res.get("success") else 400)

    field_data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    field_data["embed_field_id"] = embed_field_id
    field_data["radio_system_id"] = radio_system_id

    upd = add_or_update_system_discord_field(db, field_data)
    if not upd.get("success"):
        return jsonify({"success": False, "message": upd.get("message", "Failed to update Discord field."), "result": []}), 400

    fetched = _fetch_discord_field_by_id(db, embed_field_id)
    return jsonify({"success": True, "message": "Discord field updated.", "result": fetched.get("result", {})}), 200


# ======================================================================
# Discord fields reorder (bulk)      POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/discord/fields/reorder", methods=["POST"])
@login_required
@csrf_protect
def systems_discord_fields_reorder(radio_system_id: int):
    """
    Bulk reorder Discord fields for a system.
    POST body: { "order": [embed_field_id, ...] }
    """
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else {"order": request.form.getlist("order")}
    raw_order = body.get("order") or []
    try:
        ordered_ids = [int(x) for x in raw_order]
    except Exception:
        return jsonify({"success": False, "message": "Invalid order payload; must be integer list.", "result": []}), 400

    res = db.execute_query(
        "SELECT discord_setting_id FROM radio_system_discord_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )
    if not res.get("success") or not res.get("result"):
        return jsonify({"success": False, "message": f"No Discord settings for system {radio_system_id}.", "result": []}), 400
    discord_setting_id = res["result"]["discord_setting_id"]

    upd = reorder_system_discord_fields(db, discord_setting_id, ordered_ids)
    if not upd.get("success"):
        return jsonify({"success": False, "message": upd.get("message", "Failed to reorder Discord fields."), "result": []}), 400

    fetched = _fetch_discord_fields_for_system(db, radio_system_id)
    fetched["message"] = "Discord field order updated." if fetched["success"] else "Discord field order updated (fetch failed)."
    return jsonify(fetched), 200

@api_systems.route("/<int:radio_system_id>/discord/test", methods=["POST"])
@login_required
def systems_discord_test(radio_system_id: int):
    """
    Send a Discord test message using the system's configured Discord settings + templates.
    Map/audio attachments are handled by DiscordSender toggles (render_map / attach_audio).
    """
    db = current_app.config["db"]

    ok, msg, system_row = _fetch_system_with_config(db, radio_system_id)
    if not ok or not system_row:
        return jsonify(success=False, message=msg or "System not found.", result=[]), 404

    # Build test payload + transcript data
    payload, fired_trigger_data, transcript_text, transcript_segments = _make_discord_test_data(radio_system_id)

    # Prefer per-system tz if you store it; otherwise default.
    tz = (system_row.get("timezone") or "America/New_York")

    # Build ctx exactly like real dispatch
    ctx = build_context(
        system_row,
        payload,
        fired_trigger_data,
        detect_result=None,  # tones_summary will just be ""
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
        tz=current_app.config["TIMEZONE"],
    )

    discord = DiscordSender.from_system_row(system_row, logger=current_app.logger)

    # Require webhook to exist
    if not discord.settings.webhook_url:
        return jsonify(success=False, message="Discord webhook URL is not configured for this system.", result=[]), 400

    # Allow explicit test sends even if Discord 'enabled' toggle is off.
    # (User clicked the button, so they clearly want to test.)
    discord.settings.enabled = True

    # Safety: if user has zero fields and no title configured, Discord may reject an empty embed.
    # Only inject a test title when it would otherwise be empty.
    if (not discord.settings.embed_title) and (not any(f.field_enabled for f in (discord.settings.fields or []))):
        discord.settings.embed_title = "[TEST] {system_name} • {trigger_list} @ {timestamp_24}"

    sent = discord.send(
        ctx,
        content=None,  # set to e.g. "[TEST] ..." if you want a visible non-embed line too
    )

    if not sent:
        return jsonify(
            success=False,
            message="Discord test send failed. Check server logs for details (discord_module).",
            result=[],
        ), 502

    return jsonify(
        success=True,
        message="Discord test message sent.",
        result={
            "system_id": radio_system_id,
            "timestamp_utc": ctx.get("timestamp_utc"),
            "render_map": bool(discord.settings.render_map),
            "attach_audio": bool(discord.settings.attach_audio),
        },
    ), 200

# =============================================================================
# Make helpers
# =============================================================================
def _fetch_make_settings_obj(db, radio_system_id: int):
    """
    Load Make settings object via get_systems(include_config=True).
    """
    resp = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    if not resp.get("success") or not resp["result"]:
        return {"success": False, "message": resp.get("message", "System not found"), "result": {}}
    make_obj = resp["result"][0].get("make", {}) or {}
    return {"success": True, "message": "Make settings retrieved.", "result": make_obj}


def _fetch_make_field_by_id(db, payload_field_id: int):
    """
    Fetch a single Make payload field by id.
    """
    q = """
        SELECT f.payload_field_id, f.make_setting_id, f.field_key,
               f.field_value, f.field_enabled, s.radio_system_id
        FROM radio_system_make_payload_fields f
                 JOIN radio_system_make_settings s ON f.make_setting_id = s.make_setting_id
        WHERE f.payload_field_id = ? \
        """
    res = db.execute_query(q, (payload_field_id,), fetch_mode="one")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": {}}
    if not res["result"]:
        return {"success": False, "message": "Field not found", "result": {}}
    return {"success": True, "message": "Field retrieved", "result": res["result"]}


def _fetch_make_fields_for_system(db, radio_system_id: int):
    """
    Fetch all Make payload fields for a system.
    """
    q = """
        SELECT f.payload_field_id, f.make_setting_id, f.field_key, f.field_value, f.field_enabled
        FROM radio_system_make_payload_fields f
                 JOIN radio_system_make_settings s ON f.make_setting_id = s.make_setting_id
        WHERE s.radio_system_id = ?
        ORDER BY f.payload_field_id ASC \
        """
    res = db.execute_query(q, (radio_system_id,), fetch_mode="all")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}
    return {"success": True, "message": "Make fields retrieved", "result": res["result"]}


# ======================================================================
# Make settings                      GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/make/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_make_settings(radio_system_id: int):
    """
    Get or update Make settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_make_settings_obj(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_make_settings(db, payload)
    if not upd.get("success"):
        return jsonify({"success": False, "message": upd["message"], "result": []}), 400

    res = _fetch_make_settings_obj(db, radio_system_id)
    res["message"] = "Make settings updated." if res["success"] else "Updated, but re-fetch failed."
    return jsonify(res), 200


# ======================================================================
# Make fields collection             GET | POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/make/fields", methods=["GET", "POST"])
@login_required
@csrf_protect
def systems_make_fields_collection(radio_system_id: int):
    """
    List or create Make payload fields for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_make_fields_for_system(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 400)

    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["radio_system_id"] = radio_system_id

    ins = add_or_update_system_make_field(db, data)
    if not ins["success"]:
        return jsonify({"success": False, "message": ins["message"], "result": []}), 400

    new_id = ins["result"]
    fetched = _fetch_make_field_by_id(db, new_id)
    return jsonify({
        "success": True,
        "message": "Make payload field added.",
        "result": fetched["result"] if fetched["success"] else {"payload_field_id": new_id}
    }), 201


# ======================================================================
# Make field item                    PATCH | DELETE
# ======================================================================
@api_systems.route("/<int:radio_system_id>/make/fields/<int:payload_field_id>", methods=["PATCH", "DELETE"])
@login_required
@csrf_protect
def systems_make_field_item(radio_system_id: int, payload_field_id: int):
    """
    Update or delete a single Make payload field.
    """
    db = current_app.config["db"]

    own_q = """
            SELECT 1
            FROM radio_system_make_payload_fields f
                     JOIN radio_system_make_settings s ON f.make_setting_id = s.make_setting_id
            WHERE f.payload_field_id = ? AND s.radio_system_id = ? \
            """
    own = db.execute_query(own_q, (payload_field_id, radio_system_id), fetch_mode="one")
    if not own.get("success"):
        return jsonify(success=False, message=own.get("message", "DB error validating field."), result=[]), 400
    if not own["result"]:
        return jsonify(success=False, message=f"Field {payload_field_id} does not belong to system {radio_system_id}.", result=[]), 404

    if request.method == "DELETE":
        res = delete_system_make_field(db, payload_field_id=payload_field_id)
        return jsonify(success=res["success"], message=res.get("message", "Deleted."), result=[]), (200 if res["success"] else 400)

    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["payload_field_id"] = payload_field_id
    data["radio_system_id"] = radio_system_id

    upd = add_or_update_system_make_field(db, data)
    if not upd["success"]:
        return jsonify(success=False, message=upd.get("message", "Failed to update Make payload field."), result=[]), 400

    fetched = _fetch_make_field_by_id(db, payload_field_id)
    return jsonify(success=True, message="Make payload field updated.", result=fetched.get("result", {})), 200

@api_systems.route("/<int:radio_system_id>/make/test", methods=["POST"])
@login_required
@csrf_protect
def systems_make_test(radio_system_id: int):
    """
    Send a Make test message using the system's configured Make settings/templates,
    by building ctx exactly like production and calling MakeSender.send().
    """
    db = current_app.config["db"]

    ok, msg, system_row = _fetch_system_with_config(db, radio_system_id)
    if not ok or not system_row:
        return jsonify(success=False, message=msg or "System not found.", result=[]), 404

    # Build test payload + transcript data (your util.py test dicts)
    payload, fired_trigger_data, transcript_text, transcript_segments = _make_discord_test_data(radio_system_id)

    # Prefer per-system tz if you store it; otherwise default.
    tz = (system_row.get("timezone") or current_app.config.get("TIMEZONE") or "America/New_York")

    # Build ctx exactly like real dispatch
    ctx = build_context(
        system_row,
        payload,
        fired_trigger_data,
        detect_result=None,
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
        tz=tz,
    )

    make = MakeSender.from_system_row(system_row, logger=current_app.logger)

    # Require webhook URL to exist
    if not make.settings.webhook_url:
        return jsonify(success=False, message="Make webhook URL is not configured for this system.", result=[]), 400

    # User clicked the button → allow test even if enabled toggle is off.
    make.settings.enabled = True

    # If you want a clearer error than "empty payload after expansion"
    enabled_fields = [f for f in (make.settings.fields or []) if f.field_enabled and (f.field_key or "").strip()]
    if not enabled_fields:
        return jsonify(
            success=False,
            message="No Make payload fields are enabled for this system (nothing to send).",
            result=[],
        ), 400

    sent = make.send(ctx, timeout_s=12, max_retries=0)

    if not sent:
        return jsonify(
            success=False,
            message="Make test send failed. Check server logs for details (make_module).",
            result=[],
        ), 502

    return jsonify(
        success=True,
        message="Make test payload sent.",
        result={
            "system_id": radio_system_id,
            "timestamp_utc": ctx.get("timestamp_utc"),
            "enabled_field_count": len(enabled_fields),
        },
    ), 200

# ======================================================================
# Pushover settings (system)         GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/pushover/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_pushover_settings(radio_system_id: int):
    """
    Get or update Pushover settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = get_systems(db, radio_system_id=radio_system_id, include_config=True)
        ok = bool(res.get("success") and res["result"])
        payload = res["result"][0]["pushover"] if ok else {}
        return jsonify(success=ok, message=res.get("message", "Not found" if not ok else "OK"), result=payload), (200 if ok else 404)

    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["radio_system_id"] = radio_system_id

    upd = update_system_pushover_settings(db, data)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    refetch = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    pushover = refetch["result"][0]["pushover"] if refetch["success"] else {}
    return jsonify(success=True, message="Pushover settings updated.", result=pushover), 200

@api_systems.route("/<int:radio_system_id>/pushover/test", methods=["POST"])
@login_required
@csrf_protect
def systems_pushover_test(radio_system_id: int):
    """
    Send a test Pushover notification using the configured app/group tokens.
    """
    db = current_app.config["db"]

    if requests is None:
        return jsonify(success=False, message="Python 'requests' package is not available on the server.", result=[]), 500

    ok, msg, system = _fetch_system_with_config(db, radio_system_id)
    if not ok:
        return jsonify(success=False, message=msg, result=[]), 404

    po = system.get("pushover") or {}
    app_token   = po.get("app_token")
    group_token = po.get("group_token")
    sound   = po.get("sound")   or "pushover"
    subject = po.get("subject") or "Dispatch Alert"
    body    = po.get("body")    or "Test alert from AlertPage."

    if not app_token or not group_token:
        return jsonify(success=False, message="Pushover app/group tokens must be configured.", result=[]), 400

    message = f"[TEST] {system.get('system_name','System')} (ID {radio_system_id})\n\n{body}"

    try:
        r = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": app_token,
                "user": group_token,
                "title": subject,
                "message": message,
                "sound": sound,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if int(data.get("status", 0)) != 1:
            return jsonify(success=False, message=f"Pushover API error: {data}", result=data), 400
    except Exception as exc:
        return jsonify(success=False, message=f"Error calling Pushover API: {exc}", result=[]), 502

    return jsonify(success=True, message="Pushover test notification sent.", result=[]), 200


# =============================================================================
# Email helpers
# =============================================================================
def _fetch_email_settings_obj(db, radio_system_id: int):
    """
    Return the SMTP settings object (plus recipient list) via get_systems(include_config=True).
    """
    res = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    if not res.get("success") or not res["result"]:
        return {"success": False, "message": res.get("message", "System not found."), "result": {}}
    email_obj = res["result"][0].get("email", {}) or {}
    return {"success": True, "message": "Email settings retrieved.", "result": email_obj}


def _fetch_email_field_by_id(db, email_id: int):
    """
    Fetch a single email recipient row.
    """
    q = """
        SELECT email_id, radio_system_id, email_address, enabled
        FROM radio_system_emails
        WHERE email_id = ? \
        """
    res = db.execute_query(q, (email_id,), fetch_mode="one")
    if not res["success"]:
        return {"success": False, "message": res["message"], "result": {}}
    if not res["result"]:
        return {"success": False, "message": "Recipient not found.", "result": {}}
    return {"success": True, "message": "Recipient retrieved.", "result": res["result"]}


def _fetch_email_fields_for_system(db, radio_system_id: int):
    """
    Fetch all email recipients for a system.
    """
    q = """
        SELECT email_id, email_address, enabled
        FROM radio_system_emails
        WHERE radio_system_id = ?
        ORDER BY email_id ASC \
        """
    res = db.execute_query(q, (radio_system_id,), fetch_mode="all")
    if not res["success"]:
        return {"success": False, "message": res["message"], "result": []}
    return {"success": True, "message": "Recipients retrieved.", "result": res["result"]}


# ======================================================================
# Email settings                     GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/email/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_email_settings(radio_system_id: int):
    """
    Get or update Email (SMTP/template) settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_email_settings_obj(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_email_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    refreshed = _fetch_email_settings_obj(db, radio_system_id)
    refreshed["message"] = "Email settings updated."
    return jsonify(refreshed), 200


# ======================================================================
# Email recipients collection         GET | POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/emails", methods=["GET", "POST"])
@login_required
@csrf_protect
def systems_email_recipients(radio_system_id: int):
    """
    List or create email recipients for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_email_fields_for_system(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 400)

    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["radio_system_id"] = radio_system_id

    ins = add_or_update_system_email(db, data)
    if not ins["success"]:
        return jsonify(success=False, message=ins["message"], result=[]), 400

    new_id = ins["result"]
    fetched = _fetch_email_field_by_id(db, new_id)
    return jsonify(success=True,
                   message="Recipient added.",
                   result=fetched["result"] if fetched["success"] else {"email_id": new_id}), 201


# ======================================================================
# Email recipient item                PATCH | DELETE
# ======================================================================
@api_systems.route("/<int:radio_system_id>/emails/<int:email_id>", methods=["PATCH", "DELETE"])
@login_required
@csrf_protect
def systems_email_recipient_item(radio_system_id: int, email_id: int):
    """
    Update or delete a single email recipient.
    """
    db = current_app.config["db"]

    own = db.execute_query(
        "SELECT 1 FROM radio_system_emails WHERE email_id = ? AND radio_system_id = ?",
        (email_id, radio_system_id),
        fetch_mode="one"
    )
    if not own["success"]:
        return jsonify(success=False, message=own["message"], result=[]), 400
    if not own["result"]:
        return jsonify(success=False, message="Recipient not found for this system.", result=[]), 404

    if request.method == "DELETE":
        res = delete_system_email(db, email_id=email_id)
        return jsonify(success=res["success"], message=res.get("message", "Deleted."), result=[]), (200 if res["success"] else 400)

    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["email_id"] = email_id
    data["radio_system_id"] = radio_system_id

    upd = add_or_update_system_email(db, data)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    fetched = _fetch_email_field_by_id(db, email_id)
    return jsonify(success=True, message="Recipient updated.", result=fetched.get("result", {})), 200

@api_systems.route("/<int:radio_system_id>/email/test", methods=["POST"])
@login_required
@csrf_protect
def systems_email_test(radio_system_id: int):
    """
    Send a test email using the configured SMTP settings and recipients.
    """
    db = current_app.config["db"]

    ok, msg, system = _fetch_system_with_config(db, radio_system_id)
    if not ok:
        return jsonify(success=False, message=msg, result=[]), 404

    email_cfg = system.get("email") or {}

    host = (email_cfg.get("smtp_hostname") or "").strip()
    port = int(email_cfg.get("smtp_port") or 0)
    user = (email_cfg.get("smtp_username") or "").strip()
    password = email_cfg.get("smtp_password") or ""
    from_addr = (email_cfg.get("email_address_from") or user or "").strip()
    from_name = (email_cfg.get("email_text_from") or "").strip()

    if not host or not port or not from_addr:
        return jsonify(success=False, message="SMTP host, port, and 'from' address must be configured.", result=[]), 400

    # recipients: enabled only
    recipients = [
        r["email_address"]
        for r in (email_cfg.get("recipients") or [])
        if r.get("enabled") and r.get("email_address")
    ]
    if not recipients:
        return jsonify(success=False, message="No enabled recipient email addresses configured.", result=[]), 400

    subject = email_cfg.get("email_alert_subject") or f"[AlertPage] Test alert – {system.get('system_name','System')}"
    body_tpl = email_cfg.get("email_alert_body") or "This is a test alert email from AlertPage configuration."
    body = f"[TEST] System {system.get('system_name','System')} (ID {radio_system_id})\n\n{body_tpl}"

    msg_obj = EmailMessage()
    msg_obj["Subject"] = subject
    msg_obj["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg_obj["To"] = ", ".join(recipients)
    msg_obj.set_content(body)

    context = ssl.create_default_context()

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg_obj)
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                except smtplib.SMTPException:
                    # server may not support STARTTLS – that's fine for testing
                    pass
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg_obj)
    except Exception as exc:
        return jsonify(success=False, message=f"Error sending test email: {exc}", result=[]), 502

    return jsonify(
        success=True,
        message=f"Test email sent to: {', '.join(recipients)}.",
        result=[],
    ), 200


# ======================================================================
# Transcribe settings                 GET | PATCH
# ======================================================================

def _fetch_transcribe_settings_obj(db, radio_system_id: int):
    """
    Return the Transcribe settings object for a system.

    • Reads via get_systems(include_config=True) to keep one source of truth.
    • If the child row doesn't exist yet (older systems), it creates it and re-fetches.
    """
    def _read():
        res = get_systems(db, radio_system_id=radio_system_id, include_config=True)
        if not res.get("success") or not res.get("result"):
            return res, {}
        return res, (res["result"][0].get("transcribe") or {})

    # first read
    res, obj = _read()
    if not res.get("success"):
        return {"success": False, "message": res.get("message", "Failed to fetch transcribe settings."), "result": {}}

    # if there’s no child row, create it and re-read
    has_row = bool(obj) and (obj.get("transcribe_setting_id") is not None or any(v is not None for v in obj.values()))
    if not has_row:
        ensure = db.execute_commit(
            "INSERT INTO radio_system_transcribe_settings (radio_system_id) "
            "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM radio_system_transcribe_settings WHERE radio_system_id = ?)",
            (radio_system_id, radio_system_id),
            return_row_id=False
        )
        if not ensure.get("success"):
            return {"success": False, "message": ensure.get("message", "Failed to ensure transcribe settings row."), "result": {}}
        res, obj = _read()
        if not res.get("success") or not res.get("result"):
            return {"success": False, "message": res.get("message", "Failed to re-fetch transcribe settings."), "result": {}}

    return {"success": True, "message": "Transcribe settings retrieved.", "result": obj}


@api_systems.route("/<int:radio_system_id>/transcribe/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_transcribe_settings(radio_system_id: int):
    db = current_app.config["db"]

    if request.method == "GET":
        obj = _fetch_transcribe_settings_obj(db, radio_system_id)
        return jsonify(obj), (200 if obj["success"] else 404)

    # PATCH
    raw = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    raw = raw or {}
    raw["radio_system_id"] = radio_system_id

    alias_map = {
        "enabled":  "transcribe_enabled",
        "url":      "transcribe_url",
        "api_key":  "transcribe_api_key",
        "model":    "transcribe_model",
        "language": "transcribe_language",
        "prompt":   "transcribe_prompt",
    }
    allowed = {
        "transcribe_enabled", "transcribe_url", "transcribe_api_key",
        "transcribe_model", "transcribe_language", "transcribe_prompt",
        "radio_system_id",
    }

    payload = {}
    for k, v in raw.items():
        key = alias_map.get(k, k)
        if key in allowed:
            payload[key] = v

    # No coercion here; system_module handles normalization using _bool_like/_norm_str
    upd = update_system_transcribe_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    obj = _fetch_transcribe_settings_obj(db, radio_system_id)
    obj["message"] = "Transcribe settings updated." if obj["success"] else "Updated, but re-fetch failed."
    return jsonify(obj), 200

@api_systems.route("/<int:radio_system_id>/transcribe/test", methods=["POST"])
@login_required
@csrf_protect
def systems_transcribe_test(radio_system_id: int):
    """
    Make a lightweight request to the transcribe URL to verify reachability / auth.
    """
    db = current_app.config["db"]

    if requests is None:
        return jsonify(success=False, message="Python 'requests' package is not available on the server.", result=[]), 500

    ok, msg, system = _fetch_system_with_config(db, radio_system_id)
    if not ok:
        return jsonify(success=False, message=msg, result=[]), 404

    t = system.get("transcribe") or {}
    url = (t.get("url") or "").strip()
    api_key = (t.get("api_key") or "").strip()

    if not url:
        return jsonify(success=False, message="Transcribe URL is not configured.", result=[]), 400
    if not api_key:
        return jsonify(success=False, message="Transcribe API key is not configured.", result=[]), 400

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    # Simple GET; if your API needs POST or a different path, tweak here.
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except Exception as exc:
        return jsonify(success=False, message=f"Error calling transcribe endpoint: {exc}", result=[]), 502

    if 200 <= r.status_code < 300:
        return jsonify(success=True, message=f"Transcribe endpoint reachable (HTTP {r.status_code}).", result=[]), 200

    return jsonify(
        success=False,
        message=f"Transcribe endpoint returned HTTP {r.status_code}: {r.text[:200]}",
        result=[],
    ), 400


# ======================================================================
# Upload/Split settings               GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/upload/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_upload_settings(radio_system_id: int):
    """
    Get or update upload/split-dispatch settings for a system.
    """
    db = current_app.config["db"]

    def _fetch_upload_settings_obj():
        q = """
            SELECT split_enabled, tail_min_voice_sec, vad_min_speech_ratio,
                   voice_rms_dbfs, max_split_interval, max_split_length, audio_min_length
            FROM radio_system_upload_settings
            WHERE radio_system_id = ? \
            """
        res = db.execute_query(q, (radio_system_id,), fetch_mode="one")
        if not res["success"]:
            return {"success": False, "message": res["message"], "result": {}}

        if not res["result"]:
            ins = db.execute_commit(
                "INSERT INTO radio_system_upload_settings (radio_system_id) VALUES (?)",
                (radio_system_id,), return_row_id=False
            )
            if not ins["success"]:
                return {"success": False, "message": ins["message"], "result": {}}
            res = db.execute_query(q, (radio_system_id,), fetch_mode="one")

        return {"success": True, "message": "Upload settings retrieved.", "result": res["result"]}

    if request.method == "GET":
        obj = _fetch_upload_settings_obj()
        return jsonify(obj), (200 if obj["success"] else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_upload_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    obj = _fetch_upload_settings_obj()
    obj["message"] = "Upload settings updated." if obj["success"] else "Updated, but re-fetch failed."
    return jsonify(obj), 200


# ======================================================================
# TRIGGERS: new condition-sets model (no legacy flat tone columns)
# ======================================================================

# ─────────────────────────────────────────────────────────────────────
# Collection:  /api/systems/<sys_id>/triggers     GET | POST
# ─────────────────────────────────────────────────────────────────────
@api_systems.route("/<int:radio_system_id>/triggers", methods=["GET", "POST"], strict_slashes=False)
@login_required
@csrf_protect
def triggers_collection(radio_system_id: int):
    """
    List or create triggers for a system (new condition-sets model).

    GET query params:
      - alert_trigger_id            : int or comma-list
      - alert_trigger_name          : str or comma-list (exact)
      - alert_trigger_talkgroup     : int or comma-list
      - full                        : 0/1 | true/false  (include child rule sets)

    POST body (JSON or form):
      - Main row metadata (subset only): name, type, enabled flags, ignore_time,
        tone_tolerance, talkgroup, stream_url
      - Child arrays (optional): two_tone_sets, long_tone_sets, hi_low_sets,
        pulsed_sets, dtmf_sequences

    Notes:
      • Legacy flat tone columns are ignored here (they were removed from DB).
      • If child arrays are present, they are applied via UPSERT-by-rule_uid.
    """
    db = current_app.config["db"]

    # ---------- GET (list) ---------------------------------------------------
    if request.method == "GET":
        trig_id   = _parse_int_or_list(request.args.get("alert_trigger_id"))
        trig_name = _parse_str_or_list(request.args.get("alert_trigger_name"))
        trig_tg   = _parse_int_or_list(request.args.get("alert_trigger_talkgroup"))
        want_full = (request.args.get("full", "0").lower() in ("1", "true", "yes"))

        if want_full:
            resp = get_triggers_full(
                db,
                radio_system_id=radio_system_id,
                alert_trigger_id=trig_id,
                alert_trigger_name=trig_name,
                alert_trigger_talkgroup=trig_tg
            )
        else:
            resp = get_triggers(
                db,
                radio_system_id=radio_system_id,
                alert_trigger_id=trig_id,
                alert_trigger_name=trig_name,
                alert_trigger_talkgroup=trig_tg
            )
        return jsonify(resp), (200 if resp["success"] else 400)

    # ---------- POST (create) ------------------------------------------------
    raw = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    raw = raw or {}
    raw["radio_system_id"] = radio_system_id   # enforce FK

    # NEW: make sure system-level channel rows exist (and seed defaults once)
    _ensure_system_channel_rows(db, radio_system_id)
    _seed_default_channel_fields(db, radio_system_id)

    # Parse child arrays if form-data delivered JSON strings
    _coerce_child_arrays(raw)

    # Whitelist main metadata and merge included child arrays
    payload = {**_normalize_trigger_metadata(raw), "radio_system_id": radio_system_id}
    for k in _CHILD_KEYS:
        if k in raw:
            payload[k] = raw[k]

    resp = add_trigger(db, payload)
    if not resp["success"]:
        abort(400, resp["message"])

    trig_id = resp["result"]

    # NEW: ensure trigger-level child rows exist (pushover row, etc.)
    _ensure_trigger_child_rows(db, trig_id)

    return jsonify({
        "success": True,
        "message": f"Trigger '{payload.get('alert_trigger_name','')}' created.",
        "result": {"alert_trigger_id": trig_id}
    }), 201



# ─────────────────────────────────────────────────────────────────────
# Item:  /api/systems/<sys_id>/triggers/<id>   GET | PATCH | DELETE
# ─────────────────────────────────────────────────────────────────────
@api_systems.route("/<int:radio_system_id>/triggers/<int:alert_trigger_id>",
                   methods=["GET", "PATCH", "DELETE"])
@login_required
@csrf_protect
def trigger_item(radio_system_id: int, alert_trigger_id: int):
    """
    Get, update or delete a single trigger belonging to a system.

    GET query params:
      - full: 0/1 | true/false  (include child rule sets)

    PATCH body (JSON or form):
      - Any subset of main metadata fields and/or any of the child arrays.
        When a child array is present, it fully replaces that category
        (server upserts by rule_uid and prunes orphans).
    """
    db = current_app.config["db"]

    # sanity: ensure this trigger belongs to the system
    owner = db.execute_query(
        "SELECT 1 FROM alert_triggers WHERE alert_trigger_id = ? AND radio_system_id = ?",
        (alert_trigger_id, radio_system_id), fetch_mode="one"
    )
    if not owner["success"]:
        abort(400, owner["message"])
    if not owner["result"]:
        abort(404, "Trigger not found for this system")

    # ---------- GET ---------------------------------------------------------
    if request.method == "GET":
        want_full = (request.args.get("full", "0").lower() in ("1", "true", "yes"))
        resp = get_triggers_full(db, alert_trigger_id=alert_trigger_id) if want_full \
            else get_triggers(db, alert_trigger_id=alert_trigger_id)

        if not resp["success"] or not resp["result"]:
            abort(404, resp.get("message", "Trigger not found"))
        return jsonify({"success": True, "message": "OK", "result": resp["result"][0]}), 200

    # ---------- PATCH -------------------------------------------------------
    if request.method == "PATCH":
        raw = request.get_json(silent=True) if request.is_json else request.form.to_dict()
        raw = raw or {}

        # Parse child arrays if form-data sent JSON strings
        _coerce_child_arrays(raw)


        # Payload = allowed metadata + any child arrays supplied
        payload = _normalize_trigger_metadata(raw)
        for k in _CHILD_KEYS:
            if k in raw:
                payload[k] = raw[k]

        # ensure per-system channel rows exist and seed defaults (idempotent)
        _ensure_system_channel_rows(db, radio_system_id)
        _seed_default_channel_fields(db, radio_system_id)

        # ensure this trigger has its child rows (e.g., pushover) available
        _ensure_trigger_child_rows(db, alert_trigger_id)

        ts_row = _fetch_system_tone_settings_row(db, radio_system_id)
        sys_defs = _system_defaults_from_row(ts_row) if ts_row else {}
        for key in ("two_tone_sets","long_tone_sets","hi_low_sets","pulsed_sets","dtmf_sequences"):
            if key in raw and isinstance(raw[key], list):
                payload[key] = [
                    _apply_system_defaults_to_rule(key, _canonicalize_rule(key, item), sys_defs)
                    for item in (raw[key] or [])
                ]

        resp = patch_trigger(db, alert_trigger_id, payload)
        if not resp["success"]:
            abort(400, resp["message"])

        # Return updated row; respect ?full to return children if asked
        want_full = (request.args.get("full", "0").lower() in ("1", "true", "yes"))
        updated = get_triggers_full(db, alert_trigger_id=alert_trigger_id) if want_full \
            else get_triggers(db, alert_trigger_id=alert_trigger_id)

        return jsonify({
            "success": True,
            "message": "Trigger updated.",
            "result": updated["result"][0] if updated.get("result") else {}
        }), 200

    # ---------- DELETE ------------------------------------------------------
    resp = delete_trigger(db, alert_trigger_id)
    if not resp["success"]:
        abort(400, resp["message"])
    return jsonify({"success": True, "message": "Trigger deleted.", "result": []}), 200


# ======================================================================
# Trigger pushover settings           GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/triggers/<int:alert_trigger_id>/pushover/settings", methods=["GET", "PATCH"], strict_slashes=False)
@login_required
@csrf_protect
def trigger_pushover(radio_system_id: int, alert_trigger_id: int):
    """
    Get or update the Pushover child settings row for a trigger.
    Auto-creates the child row if missing.
    """
    db = current_app.config["db"]

    owner = db.execute_query(
        "SELECT 1 FROM alert_triggers WHERE alert_trigger_id = ? AND radio_system_id = ?",
        (alert_trigger_id, radio_system_id), fetch_mode="one"
    )
    if not owner["success"]:
        abort(400, owner["message"])
    if not owner["result"]:
        abort(404, "Trigger not found for this system.")

    ensure = db.execute_commit(
        """
        INSERT INTO alert_trigger_pushover_settings (alert_trigger_id)
        SELECT ? WHERE NOT EXISTS (
            SELECT 1 FROM alert_trigger_pushover_settings WHERE alert_trigger_id = ?
        )
        """,
        (alert_trigger_id, alert_trigger_id),
        return_row_id=False,
    )
    if not ensure["success"]:
        abort(400, ensure["message"])

    if request.method == "GET":
        row = get_pushover_settings(db, alert_trigger_id)
        if not row["success"]:
            abort(400, row["message"])
        return jsonify(success=True, message="OK", result=row["result"]), 200

    incoming = (request.get_json(silent=True)
                if request.content_type and request.content_type.startswith("application/json")
                else request.form.to_dict())

    ok = patch_pushover(db, alert_trigger_id, incoming)
    if not ok["success"]:
        abort(400, ok["message"])

    latest = get_pushover_settings(db, alert_trigger_id)
    return jsonify(success=True, message="Pushover settings updated.", result=latest["result"]), 200


# ─────────────────────────────────────────────────────────────────────
# Rules: /api/systems/<sys_id>/triggers/<id>/rules       POST (append one)
# ─────────────────────────────────────────────────────────────────────
@api_systems.route(
    "/<int:radio_system_id>/triggers/<int:alert_trigger_id>/rules",
    methods=["POST"],
    strict_slashes=False
)
@login_required
@csrf_protect
def trigger_rules_collection(radio_system_id: int, alert_trigger_id: int):
    """
    Append a single rule row to an existing trigger (new multi-set format).

    Body (JSON or form):
      {
        "type_key": "two_tone" | "two_tone_sets" |
                    "long"     | "long_tone"     | "long_tone_sets" |
                    "hi_low"   | "hi_low_sets"   |
                    "pulsed"   | "pulsed_sets"   |
                    "dtmf"     | "dtmf_sequences",
        "rule": {
          // EITHER canonical fields...
          //   two_tone:  freq_a_hz, min_len_a_s, freq_b_hz, min_len_b_s, tol_pct?
          //   long:      freq_hz,   min_len_s,   tol_pct?
          //   hi_low:    hi_freq_a_hz, hi_freq_b_hz, min_alternations, interval_s, tol_pct?
          //   pulsed:    center_hz, min_cycles, min_on_ms?, max_on_ms?, min_off_ms?, max_off_ms?, tol_pct?
          //   dtmf:      sequence, match_type("EXACT"|"PREFIX"|"CONTAINS"), max_gap_ms?
          //
          // ...OR friendly aliases (we canonicalize for you):
          //   two_tone:  a, a_len, b, b_len, tol
          //   long:      f, min_len, tol
          //   hi_low:    a, b, alternations, interval, tol
          //   pulsed:    f, on_ms, off_ms, tol   (single values mapped to min/max pairs)
          //   dtmf:      digit|digits, match_type|match, max_gap_ms
        }
      }

    Returns:
      { success, message, result: { alert_trigger_id, type_key, index, rule } }
    """
    db = current_app.config["db"]

    # Ensure the trigger belongs to this system
    own = db.execute_query(
        "SELECT 1 FROM alert_triggers WHERE alert_trigger_id = ? AND radio_system_id = ?",
        (alert_trigger_id, radio_system_id),
        fetch_mode="one"
    )
    if not own.get("success"):
        return jsonify(success=False, message=own.get("message", "DB error."), result={}), 400
    if not own.get("result"):
        return jsonify(success=False, message="Trigger not found for this system.", result={}), 404

    # Parse incoming
    incoming = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    type_key_raw = (incoming or {}).get("type_key", "").strip().lower()
    rule_raw     = (incoming or {}).get("rule") or {}

    # Map friendly keys → stored child-array keys
    TYPE_MAP = {
        "two_tone": "two_tone_sets",
        "two_tone_sets": "two_tone_sets",
        "long": "long_tone_sets",
        "long_tone": "long_tone_sets",
        "long_tone_sets": "long_tone_sets",
        "hi_low": "hi_low_sets",
        "hi_low_sets": "hi_low_sets",
        "pulsed": "pulsed_sets",
        "pulsed_sets": "pulsed_sets",
        "dtmf": "dtmf_sequences",
        "dtmf_sequences": "dtmf_sequences",
    }

    tk = TYPE_MAP.get(type_key_raw)
    if not tk:
        return jsonify(success=False, message="Invalid or unsupported type_key.", result={}), 400
    if not isinstance(rule_raw, dict):
        return jsonify(success=False, message="Missing or invalid 'rule' object.", result={}), 400

    # Canonicalize + tolerant numeric normalization
    rule = _canonicalize_rule(tk, rule_raw)

    ts_row = _fetch_system_tone_settings_row(db, radio_system_id)
    sys_defs = _system_defaults_from_row(ts_row) if ts_row else {}
    rule = _apply_system_defaults_to_rule(tk, rule, sys_defs)

    # Fetch current full trigger so we can append safely
    cur = get_triggers_full(db, alert_trigger_id=alert_trigger_id)
    if not cur.get("success") or not cur.get("result"):
        return jsonify(success=False, message=cur.get("message", "Failed to load trigger."), result={}), 400

    cur_row = cur["result"][0]
    existing = list(cur_row.get(tk) or [])
    existing.append(rule)

    # Persist via patch_trigger (module will upsert by rule_uid and prune)
    payload = {tk: existing}
    upd = patch_trigger(db, alert_trigger_id, payload)
    if not upd.get("success"):
        return jsonify(success=False, message=upd.get("message", "Failed to update trigger."), result={}), 400

    # Return the updated item index + echo
    new_index = len(existing) - 1
    return jsonify(
        success=True,
        message="Rule added.",
        result={
            "alert_trigger_id": alert_trigger_id,
            "type_key": tk,
            "index": new_index,
            "rule": rule
        }
    ), 201

def _fetch_system_tone_settings_row(db, radio_system_id: int) -> dict:
    """
    Try a few likely tables/shapes to get the system's tone settings row.
    Returns {} if not found.
    """
    for table in ("radio_system_tone_settings", "system_tone_settings", "radio_systems"):
        res = db.execute_query(
            f"SELECT * FROM {table} WHERE radio_system_id=?",
            (radio_system_id,), fetch_mode="one"
        )
        if res.get("success") and res.get("result"):
            return res["result"]
    return {}

def _system_defaults_from_row(ts: dict) -> dict:
    """Build the same defaults map the client uses."""
    def pick_num(keys: list[str]):
        for k in keys:
            v = ts.get(k)
            if v is not None:
                try: return float(v)
                except (TypeError, ValueError): pass
        return None

    return {
        "two_tone": {
            "min_len_a_s": pick_num(["two_tone_a_min_s","two_tone_min_len_a_s","min_len_a_s"]),
            "min_len_b_s": pick_num(["two_tone_b_min_s","two_tone_min_len_b_s","min_len_b_s"]),
        },
        "long_tone": {
            "min_len_s": pick_num(["long_min_s","long_tone_min_len_s","min_len_s"]),
        },
        "hi_low": {
            "min_alternations": pick_num(["hi_low_min_alternations","hi_low_min_alts","min_alternations"]),
            "interval_s":       pick_num(["hi_low_interval_s","interval_s"]),
        },
        "pulsed": {
            "min_cycles": pick_num(["pulsed_min_cycles","min_cycles"]),
            "min_on_ms":  pick_num(["pulsed_min_on_ms","min_on_ms","on_min_ms"]),
            "max_on_ms":  pick_num(["pulsed_max_on_ms","max_on_ms","on_max_ms"]),
            "min_off_ms": pick_num(["pulsed_min_off_ms","min_off_ms","off_min_ms"]),
            "max_off_ms": pick_num(["pulsed_max_off_ms","max_off_ms","off_max_ms"]),
        }
    }

def _apply_system_defaults_to_rule(type_key: str, rule: dict, sys_defaults: dict) -> dict:
    """
    For newly added rules, fill missing (None/"") fields from system defaults.
    We deliberately DO NOT backfill tol_pct (keeps the inheritance behavior).
    """
    tk = {
        "two_tone_sets": "two_tone",
        "long_tone_sets": "long_tone",
        "hi_low_sets": "hi_low",
        "pulsed_sets": "pulsed",
        "dtmf_sequences": "dtmf",
    }.get(type_key, type_key)

    dmap = sys_defaults.get(tk) or {}
    if not dmap:
        return rule

    def needs(v): return v is None or v == ""

    if tk == "two_tone":
        if needs(rule.get("min_len_a_s")): rule["min_len_a_s"] = dmap.get("min_len_a_s")
        if needs(rule.get("min_len_b_s")): rule["min_len_b_s"] = dmap.get("min_len_b_s")
    elif tk == "long_tone":
        if needs(rule.get("min_len_s")):   rule["min_len_s"]   = dmap.get("min_len_s")
    elif tk == "hi_low":
        if needs(rule.get("min_alternations")): rule["min_alternations"] = dmap.get("min_alternations")
        if needs(rule.get("interval_s")):       rule["interval_s"]       = dmap.get("interval_s")
    elif tk == "pulsed":
        for k in ("min_cycles","min_on_ms","max_on_ms","min_off_ms","max_off_ms"):
            if needs(rule.get(k)): rule[k] = dmap.get(k)

    return rule

# ======================================================================
# n8n /api/systems/<int:radio_system_id>/n8n/settings   GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/n8n/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_n8n_settings(radio_system_id: int):
    db = current_app.config["db"]

    if request.method == "GET":
        res = get_system_n8n_settings(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 404)

    # PATCH (STANDARD keys only)
    raw = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    raw = raw or {}

    # allow nested or flat; always pass radio_system_id
    payload = raw.get("n8n") if isinstance(raw.get("n8n"), dict) else raw

    res = update_system_n8n_settings(
        db,
        {
            "radio_system_id": radio_system_id,
            "n8n": payload,
        },
    )
    return jsonify(res), (200 if res["success"] else 400)

@api_systems.route("/<int:radio_system_id>/n8n/test", methods=["POST"])
@login_required
@csrf_protect
def systems_n8n_test(radio_system_id: int):
    """
    Send an n8n test message using the system's configured n8n settings,
    by building ctx exactly like production and calling N8nSender.send().
    """
    db = current_app.config["db"]

    ok, msg, system_row = _fetch_system_with_config(db, radio_system_id)
    if not ok or not system_row:
        return jsonify(success=False, message=msg or "System not found.", result=[]), 404

    # Build test payload + transcript data (your util.py test dicts)
    payload, fired_trigger_data, transcript_text, transcript_segments = _make_discord_test_data(radio_system_id)

    # Prefer per-system tz if you store it; otherwise default.
    tz = (system_row.get("timezone") or current_app.config.get("TIMEZONE") or "America/New_York")

    # Build ctx exactly like real dispatch
    ctx = build_context(
        system_row,
        payload,
        fired_trigger_data,
        detect_result=None,  # tones_summary will just be ""
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
        tz=tz,
    )

    n8n = N8nSender.from_system_row(system_row, logger=current_app.logger)

    # Require config to exist
    if not n8n.settings.webhook_url:
        return jsonify(success=False, message="n8n webhook URL is not configured for this system.", result=[]), 400

    # User clicked the button → allow test even if the "enabled" toggle is off
    n8n.settings.enabled = True

    # Build map_image_url exactly like production dispatch
    base = os.getenv("PUBLIC_MAP_BASE_URL", "").rstrip("/")
    lat = ctx.get("address_lat")
    lng = ctx.get("address_lng")
    incident = ctx.get("incident_category") or "Other"
    if base and lat is not None and lng is not None:
        ctx["map_image_url"] = (
            f"{base}/map-image?lat={lat}&lng={lng}&incident={incident}"
        )

    # “As if production”:
    # - Test always sends regardless of trigger n8n gate flags
    sent = n8n.send(
        ctx,
        fired_trigger_data=fired_trigger_data,
        only_if_any_trigger_enabled=False,
        max_retries=0,  # test button: don't sit and retry
    )

    if not sent:
        return jsonify(
            success=False,
            message="n8n test send failed. Check server logs for details (n8n_module).",
            result=[],
        ), 400

    return jsonify(
        success=True,
        message="n8n test message sent.",
        result={
            "system_id": radio_system_id,
            "timestamp_utc": ctx.get("timestamp_utc"),
            "webhook_url": n8n.settings.webhook_url,  # keep/remove depending on how much you want to reveal in UI
        },
    ), 200


# ======================================================================
# Ntfy settings          GET | PATCH
# ======================================================================}
@api_systems.route("/<int:radio_system_id>/ntfy/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_ntfy_settings(radio_system_id: int):
    """
    Get or update Ntfy settings for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = get_systems(db, radio_system_id=radio_system_id, include_config=True)
        ok = bool(res.get("success") and res["result"])
        ntfy = res["result"][0]["ntfy"] if ok else {}
        return jsonify(success=ok,
                       message=res.get("message", "Not found" if not ok else "OK"),
                       result=ntfy), (200 if ok else 404)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload["radio_system_id"] = radio_system_id

    upd = update_system_ntfy_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    refetch = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    ntfy = refetch["result"][0]["ntfy"] if refetch["success"] else {}
    return jsonify(success=True, message="Ntfy settings updated.", result=ntfy), 200


@api_systems.route("/<int:radio_system_id>/ntfy/test", methods=["POST"])
@login_required
@csrf_protect
def systems_ntfy_test(radio_system_id: int):
    """
    Send an Ntfy test message using the system's configured Ntfy settings.
    """
    db = current_app.config["db"]

    ok, msg, system_row = _fetch_system_with_config(db, radio_system_id)
    if not ok or not system_row:
        return jsonify(success=False, message=msg or "System not found.", result=[]), 404

    payload, fired_trigger_data, transcript_text, transcript_segments = _make_discord_test_data(radio_system_id)
    tz = (system_row.get("timezone") or current_app.config.get("TIMEZONE") or "America/New_York")

    ctx = build_context(
        system_row,
        payload,
        fired_trigger_data,
        detect_result=None,
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
        tz=tz,
    )

    ntfy = NtfySender.from_system_row(system_row, logger=current_app.logger)

    # Build a dummy fired_trigger_data with enable_ntfy=1 so the test always sends
    test_trigger_data = [dict(t, alert_trigger_enable_ntfy=1) for t in fired_trigger_data]
    ntfy.settings.enabled = True

    sent = ntfy.send(ctx, fired_trigger_data=test_trigger_data, max_retries=0)

    if not sent:
        return jsonify(
            success=False,
            message="Ntfy test send failed. Check server logs for details.",
            result=[],
        ), 502

    return jsonify(
        success=True,
        message="Ntfy test message sent.",
        result={"system_id": radio_system_id, "sent_count": sent},
    ), 200


# ======================================================================
# Address Extraction settings          GET | PATCH
# ======================================================================}
@api_systems.route("/<int:radio_system_id>/address_extraction/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_address_extraction_settings(radio_system_id: int):
    """
    Get or update address extraction settings for a system.

    Includes geocoding regions when fetching.
    """
    db = current_app.config["db"]

    # ------------- GET -------------
    if request.method == "GET":
        raw_res = get_system_address_extraction_settings(
            db,
            radio_system_id=radio_system_id,
            include_regions=True
        )

        if not raw_res.get("success") or not raw_res.get("result"):
            return jsonify(
                success=False,
                message=raw_res.get("message", "No settings found."),
                result=None,
            ), 404

        row = raw_res["result"]

        # Shape it to match your desired "address_extraction" object
        address_payload = {
            "address_extraction_setting_id": row["address_extraction_setting_id"],
            "enabled": row["address_extraction_enabled"],   # rename
            "geocode_city": row["geocode_city"],
            "geocode_country": row["geocode_country"],
            "geocode_state": row["geocode_state"],
            "google_maps_api_key": row["google_maps_api_key"],
            "nominatim_base_url": row.get("nominatim_base_url"),
            "openai_api_key": row["openai_api_key"],
            "openai_model": row["openai_model"],
            "bounds_min_lat": row["bounds_min_lat"],
            "bounds_max_lat": row["bounds_max_lat"],
            "bounds_min_lng": row["bounds_min_lng"],
            "bounds_max_lng": row["bounds_max_lng"],
            "regions": row.get("regions", []),
            "cities": row.get("cities", []),
            "roads": row.get("roads", []),
        }

        return jsonify(
            success=True,
            message="Settings retrieved successfully.",
            result=address_payload,
        ), 200

    # ------------- PATCH -------------
    # For PATCH, accept aliases, update DB, then re-fetch and reshape the same way
    raw = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    raw = raw or {}

    alias_map = {
        "enabled": "address_extraction_enabled",
        "openai_key": "openai_api_key",
        "google_key": "google_maps_api_key",
        "country": "geocode_country",
        "state": "geocode_state",
        "city": "geocode_city",
        "nominatim_url": "nominatim_base_url",
    }

    allowed = {
        "address_extraction_enabled", "openai_api_key", "openai_model",
        "google_maps_api_key", "nominatim_base_url", "geocode_country", "geocode_state", "geocode_city",
        "bounds_min_lat", "bounds_max_lat", "bounds_min_lng", "bounds_max_lng",
        "radio_system_id",
    }

    payload = {}
    for k, v in raw.items():
        key = alias_map.get(k, k)
        if key in allowed:
            payload[key] = v

    payload["radio_system_id"] = radio_system_id

    upd = update_system_address_extraction_settings(db, payload)
    if not upd["success"]:
        return jsonify(success=False, message=upd["message"], result=[]), 400

    # ---- Persist cities if provided in the same payload ----
    cities_raw = raw.get("cities")
    if isinstance(cities_raw, list) and cities_raw:
        # Resolve address_extraction_setting_id
        aes_res = db.execute_query(
            "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
            (radio_system_id,),
            fetch_mode="one"
        )
        if aes_res.get("success") and aes_res.get("result"):
            aes_id = aes_res["result"]["address_extraction_setting_id"]

            # Clear existing cities
            db.execute_commit(
                "DELETE FROM geocoding_cities WHERE address_extraction_setting_id = ?",
                (aes_id,),
                return_count=True
            )

            # Bulk insert new list
            bulk = bulk_add_geocoding_cities(
                db,
                aes_id,
                [{"city_name": c.get("city_name", "").strip(), "priority": int(c.get("priority", 0))}
                 for c in cities_raw if c.get("city_name", "").strip()]
            )
            if not bulk.get("success"):
                # Log but don't fail the whole settings save
                current_app.logger.warning(
                    "Failed to persist geocoding cities for system %s: %s",
                    radio_system_id,
                    bulk.get("message")
                )

    # Re-fetch and reshape so PATCH returns the same structure as GET
    raw_res = get_system_address_extraction_settings(
        db,
        radio_system_id=radio_system_id,
        include_regions=True
    )

    if not raw_res.get("success") or not raw_res.get("result"):
        # update succeeded but refetch failed
        return jsonify(
            success=False,
            message="Updated, but re-fetch failed.",
            result=None
        ), 200

    row = raw_res["result"]
    address_payload = {
        "address_extraction_setting_id": row["address_extraction_setting_id"],
        "enabled": row["address_extraction_enabled"],
        "geocode_city": row["geocode_city"],
        "geocode_country": row["geocode_country"],
        "geocode_state": row["geocode_state"],
        "google_maps_api_key": row["google_maps_api_key"],
        "nominatim_base_url": row.get("nominatim_base_url"),
        "openai_api_key": row["openai_api_key"],
        "openai_model": row["openai_model"],
        "bounds_min_lat": row["bounds_min_lat"],
        "bounds_max_lat": row["bounds_max_lat"],
        "bounds_min_lng": row["bounds_min_lng"],
        "bounds_max_lng": row["bounds_max_lng"],
        "regions": row.get("regions", []),
        "cities": row.get("cities", []),
    }

    return jsonify(
        success=True,
        message="Address extraction settings updated.",
        result=address_payload,
    ), 200


# =============================================================================
# Address Extraction Regions helpers
# =============================================================================
def _fetch_geocoding_region_by_id(db, region_id: int):
    """
    Fetch a single geocoding region by its primary key.
    """
    from lib.system_module import get_geocoding_regions

    res = get_geocoding_regions(db, region_id=region_id)
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": {}}

    region = res.get("result")
    if not region:
        return {"success": False, "message": "Region not found.", "result": {}}

    return {"success": True, "message": "Region retrieved.", "result": region}


def _fetch_geocoding_regions_for_system(db, radio_system_id: int):
    """
    Fetch all geocoding regions for a system.
    """
    from lib.system_module import get_geocoding_regions

    res = get_geocoding_regions(db, radio_system_id=radio_system_id)
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}

    return {"success": True, "message": "Regions retrieved.", "result": res.get("result", [])}


# ======================================================================
# Address Extraction Regions collection    GET | POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/regions", methods=["GET", "POST"])
@login_required
@csrf_protect
def systems_address_extraction_regions_collection(radio_system_id: int):
    """
    List or create geocoding regions for a system.

    GET: Returns all regions for this system
    POST: Creates a new region
      Body: {
        "state_code": "PA",
        "county_name": "Bradford",
        "priority": 10  // optional
      }
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_geocoding_regions_for_system(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 400)

    # POST - create new region
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["radio_system_id"] = radio_system_id

    ins = add_or_update_geocoding_region(db, data)
    if not ins.get("success"):
        return jsonify({
            "success": False,
            "message": ins.get("message", "Failed to add region."),
            "result": []
        }), 400

    new_id = ins.get("result")
    fetched = _fetch_geocoding_region_by_id(db, new_id) if new_id else {"success": True, "result": {}}

    return jsonify({
        "success": True,
        "message": "Geocoding region added.",
        "result": fetched["result"] if fetched.get("success") else {"region_id": new_id}
    }), 201


# ======================================================================
# Address Extraction Region item           PATCH | DELETE
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/regions/<int:region_id>",
                   methods=["PATCH", "DELETE"])
@login_required
@csrf_protect
def systems_address_extraction_region_item(radio_system_id: int, region_id: int):
    """
    Update or delete a single geocoding region.

    PATCH Body: {
      "state_code": "NY",     // optional
      "county_name": "Tioga", // optional
      "priority": 20          // optional
    }
    """
    db = current_app.config["db"]

    # Ownership / existence check
    own_q = """
            SELECT gr.region_id
            FROM geocoding_regions AS gr
                     JOIN radio_system_address_extraction_settings AS aes
                          ON gr.address_extraction_setting_id = aes.address_extraction_setting_id
            WHERE gr.region_id = ? AND aes.radio_system_id = ? \
            """
    own_res = db.execute_query(own_q, (region_id, radio_system_id), fetch_mode="one")

    if not own_res.get("success"):
        return jsonify({
            "success": False,
            "message": own_res.get("message", "DB error validating region."),
            "result": []
        }), 400

    if not own_res.get("result"):
        return jsonify({
            "success": False,
            "message": f"Region {region_id} not found for system {radio_system_id}.",
            "result": []
        }), 404

    # DELETE
    if request.method == "DELETE":
        del_res = delete_geocoding_region(db, region_id=region_id)
        return jsonify({
            "success": del_res.get("success", False),
            "message": del_res.get("message", "Failed to delete region."),
            "result": []
        }), (200 if del_res.get("success") else 400)

    # PATCH
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["region_id"] = region_id
    data["radio_system_id"] = radio_system_id

    upd = add_or_update_geocoding_region(db, data)
    if not upd.get("success"):
        return jsonify({
            "success": False,
            "message": upd.get("message", "Failed to update region."),
            "result": []
        }), 400

    fetched = _fetch_geocoding_region_by_id(db, region_id)
    return jsonify({
        "success": True,
        "message": "Geocoding region updated.",
        "result": fetched.get("result", {})
    }), 200


# ======================================================================
# Address Extraction Regions reorder (bulk)   POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/regions/reorder", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_regions_reorder(radio_system_id: int):
    """
    Bulk reorder geocoding regions for a system.

    POST body: { "order": [region_id, region_id, ...] }

    Sets priority as 10, 20, 30, ... based on array order.
    """
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else {"order": request.form.getlist("order")}
    raw_order = body.get("order") or []

    try:
        ordered_ids = [int(x) for x in raw_order]
    except Exception:
        return jsonify({
            "success": False,
            "message": "Invalid order payload; must be integer list.",
            "result": []
        }), 400

    # Get address_extraction_setting_id for this system
    res = db.execute_query(
        "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )

    if not res.get("success") or not res.get("result"):
        return jsonify({
            "success": False,
            "message": f"No address extraction settings for system {radio_system_id}.",
            "result": []
        }), 400

    address_extraction_setting_id = res["result"]["address_extraction_setting_id"]

    upd = reorder_geocoding_regions(db, address_extraction_setting_id, ordered_ids)
    if not upd.get("success"):
        return jsonify({
            "success": False,
            "message": upd.get("message", "Failed to reorder regions."),
            "result": []
        }), 400

    fetched = _fetch_geocoding_regions_for_system(db, radio_system_id)
    fetched["message"] = "Region order updated." if fetched["success"] else "Region order updated (fetch failed)."
    return jsonify(fetched), 200


# ======================================================================
# Address Extraction Regions bulk add      POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/regions/bulk", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_regions_bulk(radio_system_id: int):
    """
    Bulk add multiple geocoding regions in a single transaction.

    POST body: {
      "regions": [
        {"state_code": "PA", "county_name": "Bradford", "priority": 10},
        {"state_code": "NY", "county_name": "Tioga", "priority": 20},
        ...
      ]
    }
    """
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    regions = body.get("regions", [])

    if not isinstance(regions, list):
        return jsonify({
            "success": False,
            "message": "Invalid payload; 'regions' must be an array.",
            "result": []
        }), 400

    # Get address_extraction_setting_id for this system
    res = db.execute_query(
        "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )

    if not res.get("success") or not res.get("result"):
        return jsonify({
            "success": False,
            "message": f"No address extraction settings for system {radio_system_id}.",
            "result": []
        }), 400

    address_extraction_setting_id = res["result"]["address_extraction_setting_id"]

    result = bulk_add_geocoding_regions(db, address_extraction_setting_id, regions)

    return jsonify(result), (200 if result.get("success") else 400)


# =============================================================================
# Address Extraction Cities helpers
# =============================================================================
def _fetch_geocoding_city_by_id(db, city_id: int):
    """
    Fetch a single geocoding city by its primary key.
    """
    from lib.system_module import get_geocoding_cities

    res = get_geocoding_cities(db, city_id=city_id)
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": {}}

    city = res.get("result")
    if not city:
        return {"success": False, "message": "City not found.", "result": {}}

    return {"success": True, "message": "City retrieved.", "result": city}


def _fetch_geocoding_cities_for_system(db, radio_system_id: int):
    """
    Fetch all geocoding cities for a system.
    """
    from lib.system_module import get_geocoding_cities

    res = get_geocoding_cities(db, radio_system_id=radio_system_id)
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}

    return {"success": True, "message": "Cities retrieved.", "result": res.get("result", [])}


# ======================================================================
# Address Extraction Cities collection    GET | POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/cities", methods=["GET", "POST"])
@login_required
@csrf_protect
def systems_address_extraction_cities_collection(radio_system_id: int):
    """
    List or create geocoding cities for a system.
    """
    db = current_app.config["db"]

    if request.method == "GET":
        res = _fetch_geocoding_cities_for_system(db, radio_system_id)
        return jsonify(res), (200 if res["success"] else 400)

    # POST - create new city
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["radio_system_id"] = radio_system_id

    ins = add_or_update_geocoding_city(db, data)
    if not ins.get("success"):
        return jsonify({
            "success": False,
            "message": ins.get("message", "Failed to add city."),
            "result": []
        }), 400

    new_id = ins.get("result")
    fetched = _fetch_geocoding_city_by_id(db, new_id) if new_id else {"success": True, "result": {}}

    return jsonify({
        "success": True,
        "message": "Geocoding city added.",
        "result": fetched["result"] if fetched.get("success") else {"city_id": new_id}
    }), 201


# ======================================================================
# Address Extraction City item           PATCH | DELETE
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/cities/<int:city_id>",
                   methods=["PATCH", "DELETE"])
@login_required
@csrf_protect
def systems_address_extraction_city_item(radio_system_id: int, city_id: int):
    """
    Update or delete a single geocoding city.
    """
    db = current_app.config["db"]

    # Ownership / existence check
    own_q = """
            SELECT gc.city_id
            FROM geocoding_cities AS gc
                     JOIN radio_system_address_extraction_settings AS aes
                          ON gc.address_extraction_setting_id = aes.address_extraction_setting_id
            WHERE gc.city_id = ? AND aes.radio_system_id = ? \
            """
    own_res = db.execute_query(own_q, (city_id, radio_system_id), fetch_mode="one")

    if not own_res.get("success"):
        return jsonify({
            "success": False,
            "message": own_res.get("message", "DB error validating city."),
            "result": []
        }), 400

    if not own_res.get("result"):
        return jsonify({
            "success": False,
            "message": f"City {city_id} not found for system {radio_system_id}.",
            "result": []
        }), 404

    # DELETE
    if request.method == "DELETE":
        del_res = delete_geocoding_city(db, city_id=city_id)
        return jsonify({
            "success": del_res.get("success", False),
            "message": del_res.get("message", "Failed to delete city."),
            "result": []
        }), (200 if del_res.get("success") else 400)

    # PATCH
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["city_id"] = city_id
    data["radio_system_id"] = radio_system_id

    upd = add_or_update_geocoding_city(db, data)
    if not upd.get("success"):
        return jsonify({
            "success": False,
            "message": upd.get("message", "Failed to update city."),
            "result": []
        }), 400

    fetched = _fetch_geocoding_city_by_id(db, city_id)
    return jsonify({
        "success": True,
        "message": "Geocoding city updated.",
        "result": fetched.get("result", {})
    }), 200


# ======================================================================
# Address Extraction Cities reorder (bulk)   POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/cities/reorder", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_cities_reorder(radio_system_id: int):
    """
    Bulk reorder geocoding cities for a system.

    POST body: { "order": [city_id, city_id, ...] }
    """
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else {"order": request.form.getlist("order")}
    raw_order = body.get("order") or []

    try:
        ordered_ids = [int(x) for x in raw_order]
    except Exception:
        return jsonify({
            "success": False,
            "message": "Invalid order payload; must be integer list.",
            "result": []
        }), 400

    # Get address_extraction_setting_id for this system
    res = db.execute_query(
        "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )

    if not res.get("success") or not res.get("result"):
        return jsonify({
            "success": False,
            "message": f"No address extraction settings for system {radio_system_id}.",
            "result": []
        }), 400

    address_extraction_setting_id = res["result"]["address_extraction_setting_id"]

    upd = reorder_geocoding_cities(db, address_extraction_setting_id, ordered_ids)
    if not upd.get("success"):
        return jsonify({
            "success": False,
            "message": upd.get("message", "Failed to reorder cities."),
            "result": []
        }), 400

    fetched = _fetch_geocoding_cities_for_system(db, radio_system_id)
    fetched["message"] = "City order updated." if fetched["success"] else "City order updated (fetch failed)."
    return jsonify(fetched), 200


# ======================================================================
# Address Extraction Cities bulk add      POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/cities/bulk", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_cities_bulk(radio_system_id: int):
    """
    Bulk add multiple geocoding cities in a single transaction.
    """
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    cities = body.get("cities", [])

    if not isinstance(cities, list):
        return jsonify({
            "success": False,
            "message": "Invalid payload; 'cities' must be an array.",
            "result": []
        }), 400

    # Get address_extraction_setting_id for this system
    res = db.execute_query(
        "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )

    if not res.get("success") or not res.get("result"):
        return jsonify({
            "success": False,
            "message": f"No address extraction settings for system {radio_system_id}.",
            "result": []
        }), 400

    address_extraction_setting_id = res["result"]["address_extraction_setting_id"]

    result = bulk_add_geocoding_cities(db, address_extraction_setting_id, cities)

    return jsonify(result), (200 if result.get("success") else 400)


# ======================================================================
# Geocoding Roads
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/roads", methods=["GET"])
@login_required
def systems_address_extraction_roads_collection(radio_system_id: int):
    """GET list of geocoding roads for a system."""
    db = current_app.config["db"]
    res = get_geocoding_roads(db, radio_system_id=radio_system_id)
    return jsonify(res), (200 if res.get("success") else 400)


@api_systems.route("/<int:radio_system_id>/address_extraction/roads", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_road_add(radio_system_id: int):
    """POST add a new geocoding road."""
    db = current_app.config["db"]
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["radio_system_id"] = radio_system_id
    result = add_or_update_geocoding_road(db, data)
    return jsonify(result), (201 if result.get("success") else 400)


@api_systems.route("/<int:radio_system_id>/address_extraction/roads/<int:road_id>", methods=["PATCH"])
@login_required
@csrf_protect
def systems_address_extraction_road_item(radio_system_id: int, road_id: int):
    """PATCH update a geocoding road."""
    db = current_app.config["db"]
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data["road_id"] = road_id
    result = add_or_update_geocoding_road(db, data)
    return jsonify(result), (200 if result.get("success") else 400)


@api_systems.route("/<int:radio_system_id>/address_extraction/roads/<int:road_id>", methods=["DELETE"])
@login_required
@csrf_protect
def systems_address_extraction_road_delete(radio_system_id: int, road_id: int):
    """DELETE a geocoding road."""
    db = current_app.config["db"]
    result = delete_geocoding_road(db, road_id=road_id)
    return jsonify(result), (200 if result.get("success") else 400)


@api_systems.route("/<int:radio_system_id>/address_extraction/roads/reorder", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_roads_reorder(radio_system_id: int):
    """POST reorder geocoding roads."""
    db = current_app.config["db"]

    res = db.execute_query(
        "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )
    if not res.get("success") or not res.get("result"):
        return jsonify({"success": False, "message": "Settings not found.", "result": []}), 400

    aes_id = res["result"]["address_extraction_setting_id"]
    body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    ordered_ids = body.get("ordered_ids", [])

    if not isinstance(ordered_ids, list):
        return jsonify({"success": False, "message": "ordered_ids must be an array.", "result": []}), 400

    result = reorder_geocoding_roads(db, aes_id, ordered_ids)
    return jsonify(result), (200 if result.get("success") else 400)


@api_systems.route("/<int:radio_system_id>/address_extraction/roads/bulk", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_roads_bulk(radio_system_id: int):
    """Bulk add multiple geocoding roads."""
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    roads = body.get("roads", [])

    if not isinstance(roads, list):
        return jsonify({"success": False, "message": "Invalid payload; 'roads' must be an array.", "result": []}), 400

    res = db.execute_query(
        "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one"
    )
    if not res.get("success") or not res.get("result"):
        return jsonify({"success": False, "message": f"No settings for system {radio_system_id}.", "result": []}), 400

    aes_id = res["result"]["address_extraction_setting_id"]
    result = bulk_add_geocoding_roads(db, aes_id, roads)
    return jsonify(result), (200 if result.get("success") else 400)


@api_systems.route("/<int:radio_system_id>/address_extraction/roads/fetch-osm", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_roads_fetch_osm(radio_system_id: int):
    """
    POST {bounds: {min_lat, max_lat, min_lng, max_lng}} to preview roads from Overpass.
    Returns a list of discovered roads for admin review before importing.
    """
    from lib.osm_roads_module import preview_roads_for_bounds

    db = current_app.config["db"]
    body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    bounds = body.get("bounds", {})

    try:
        south = float(bounds["min_lat"])
        west = float(bounds["min_lng"])
        north = float(bounds["max_lat"])
        east = float(bounds["max_lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"success": False, "message": "bounds must contain min_lat, max_lat, min_lng, max_lng as floats.", "result": []}), 400

    if south >= north or west >= east:
        return jsonify({"success": False, "message": "Invalid bounds: south < north and west < east required.", "result": []}), 400

    try:
        roads, total = preview_roads_for_bounds(south, west, north, east)
    except RuntimeError as e:
        return jsonify({"success": False, "message": str(e), "result": []}), 502

    return jsonify({
        "success": True,
        "message": f"Found {total} unique named roads.",
        "result": roads,
    })


# ======================================================================
# Address Extraction Bounds compute      POST
# ======================================================================
@api_systems.route("/<int:radio_system_id>/address_extraction/bounds/compute", methods=["POST"])
@login_required
@csrf_protect
def systems_address_extraction_bounds_compute(radio_system_id: int):
    """
    Compute bounding box from existing geocoded calls for this system
    and save it to address_extraction_settings.

    POST body: { "padding": 0.05 }  (optional decimal degrees padding)
    """
    db = current_app.config["db"]

    body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    padding = 0.05
    try:
        if body and "padding" in body:
            padding = float(body["padding"])
    except Exception:
        padding = 0.05

    # Fetch existing geocoded calls for this system
    q = """
        SELECT
            (ct.address_geocoded_json::jsonb->>'lat')::float AS lat,
            (ct.address_geocoded_json::jsonb->>'lng')::float AS lng
        FROM call_records cr
        JOIN call_transcripts ct ON cr.call_id = ct.call_id
        WHERE cr.radio_system_id = ?
          AND ct.address_geocoded_json IS NOT NULL
          AND ct.address_geocoded_json <> ''
          AND (ct.address_geocoded_json::jsonb->>'lat') IS NOT NULL
          AND (ct.address_geocoded_json::jsonb->>'lng') IS NOT NULL
    """
    res = db.execute_query(q, (radio_system_id,), fetch_mode="all")
    if not res.get("success"):
        return jsonify({
            "success": False,
            "message": res.get("message", "DB error fetching geocoded calls."),
            "result": []
        }), 400

    rows = res.get("result", [])
    if not rows:
        return jsonify({
            "success": False,
            "message": "No geocoded calls found for this system to compute bounds.",
            "result": []
        }), 400

    lats = [float(r["lat"]) for r in rows if r["lat"] is not None]
    lngs = [float(r["lng"]) for r in rows if r["lng"] is not None]

    if not lats or not lngs:
        return jsonify({
            "success": False,
            "message": "No valid lat/lng coordinates found.",
            "result": []
        }), 400

    bounds = {
        "bounds_min_lat": min(lats) - padding,
        "bounds_max_lat": max(lats) + padding,
        "bounds_min_lng": min(lngs) - padding,
        "bounds_max_lng": max(lngs) + padding,
    }

    upd = update_system_address_extraction_settings(db, {
        "radio_system_id": radio_system_id,
        **bounds
    })
    if not upd.get("success"):
        return jsonify({
            "success": False,
            "message": upd.get("message", "Failed to save computed bounds."),
            "result": []
        }), 400

    return jsonify({
        "success": True,
        "message": f"Bounds computed from {len(rows)} call(s) and saved.",
        "result": bounds
    }), 200


# ======================================================================
# Storage settings                    GET | PATCH
# ======================================================================
@api_systems.route("/<int:radio_system_id>/storage/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_storage_settings(radio_system_id: int):
    """
    Get or update storage settings (LOCAL / SFTP / S3) for a system.

    GET  → returns the normalized storage config for this system
    PATCH → applies partial updates via update_system_storage_settings()
            OR, for clear-key-only requests, only deletes the SSH key.
    """
    db = current_app.config["db"]
    route_logger = current_app.config["logger"]

    # ---------- GET ----------
    if request.method == "GET":
        obj = _fetch_storage_settings_obj(db, radio_system_id)
        return jsonify(obj), (200 if obj["success"] else 404)

    # ---------- PATCH ----------
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    payload = payload or {}
    payload["radio_system_id"] = radio_system_id  # enforce URL

    # Pull out the SSH key (we do NOT send this to the DB layer)
    new_ssh_key = payload.pop("sftp_ssh_key", None)
    if isinstance(new_ssh_key, str):
        new_ssh_key = new_ssh_key.strip()
        if not new_ssh_key:
            new_ssh_key = None

    clear_flag_raw = payload.pop("sftp_clear_ssh_key", None)
    clear_ssh_key = False
    if isinstance(clear_flag_raw, str):
        clear_ssh_key = clear_flag_raw.strip().lower() in {"1", "true", "yes", "on"}
    elif isinstance(clear_flag_raw, (int, bool)):
        clear_ssh_key = bool(clear_flag_raw)

    # ----------------------------------------------------------
    # 1) CLEAR-ONLY MODE: button pressed, no other fields changed
    # ----------------------------------------------------------
    # After popping the special fields, payload should contain ONLY
    # radio_system_id for a clear-only request.
    ignored_keys = {"radio_system_id", "_csrf_token"}
    mutable_fields = {k for k in payload.keys() if k not in ignored_keys}
    clear_only = clear_ssh_key and not new_ssh_key and not mutable_fields

    route_logger.debug(
        "[storage] clear_only=%s clear_ssh_key=%s new_ssh_key=%s mutable_fields=%s payload_keys=%s",
        clear_only, clear_ssh_key, bool(new_ssh_key), mutable_fields, list(payload.keys())
    )

    if clear_only:
        try:
            _delete_system_ssh_key(radio_system_id)
        except Exception as e:
            route_logger.exception(
                "Failed to delete SSH key for radio_system_id=%s", radio_system_id
            )
            return jsonify(
                success=False,
                message=f"Failed to delete SSH key: {e}",
                result=[]
            ), 500

        # Re-fetch so front-end can refresh ssh_key_exists flag, etc.
        obj = _fetch_storage_settings_obj(db, radio_system_id)
        obj["message"] = "SSH key cleared." if obj["success"] else "SSH key cleared, but re-fetch failed."
        return jsonify(obj), (200 if obj["success"] else 500)

    # ----------------------------------------------------------
    # 2) NORMAL UPDATE MODE (save form)
    # ----------------------------------------------------------

    # Pass straight through; lib function handles validation & upsert
    upd = update_system_storage_settings(db, payload)
    if not upd.get("success"):
        return jsonify(
            success=False,
            message=upd.get("message", "Failed to update storage settings."),
            result=[]
        ), 400

    # If a new key was provided, write it to var/.ssh/{radio_system_id}/id_rsa
    if new_ssh_key:
        try:
            _write_system_ssh_key(radio_system_id, new_ssh_key)
        except Exception as e:
            route_logger.exception(
                "Failed to write SSH key for radio_system_id=%s", radio_system_id
            )
            return jsonify(
                success=False,
                message=f"Storage settings updated, but failed to save SSH key: {e}",
                result=[]
            ), 500

    obj = _fetch_storage_settings_obj(db, radio_system_id)
    obj["message"] = "Storage settings updated." if obj["success"] else "Updated, but re-fetch failed."

    return jsonify(obj), 200

def _write_system_ssh_key(radio_system_id: int, key_pem: str) -> None:
    """
    Write/overwrite the SSH private key for this system to:
      var/.ssh/{radio_system_id}/id_rsa

    Raises on failure.
    """
    key_dir = SSH_KEY_ROOT / str(radio_system_id)
    key_dir.mkdir(parents=True, exist_ok=True)

    key_path = key_dir / "id_rsa"

    # Normalize line endings and strip leading/trailing whitespace
    normalized = key_pem.strip().replace("\r\n", "\n").encode("utf-8")

    with open(key_path, "wb") as f:
        f.write(normalized)

    # Permissions: dir 700, file 600
    os.chmod(key_dir, 0o700)
    os.chmod(key_path, 0o600)

def _delete_system_ssh_key(radio_system_id: int) -> None:
    """
    Delete the SSH private key for this system, if it exists:
      var/.ssh/{radio_system_id}/id_rsa

    No-op if the file/dir doesn't exist. Raises on unexpected failure.
    """
    key_dir = SSH_KEY_ROOT / str(radio_system_id)
    key_path = key_dir / "id_rsa"

    # If there's no key, treat as success
    if key_path.exists():
        key_path.unlink()

    # Optionally remove directory if empty; ignore if not empty or missing
    try:
        key_dir.rmdir()
    except OSError:
        # Directory not empty or doesn't exist – that's fine.
        pass

def _fetch_storage_settings_obj(db, radio_system_id: int):
    """
    Ensure and return storage settings for a system.

    Uses get_system_storage_settings() and auto-creates a default row
    (LOCAL + default path pattern) if missing.
    """
    # First attempt to read
    res = get_system_storage_settings(db, radio_system_id=radio_system_id)
    if not res.get("success"):
        return {
            "success": False,
            "message": res.get("message", "Failed to fetch storage settings."),
            "result": {}
        }

    if res.get("result"):
        # Already have a row; just return it
        return {
            "success": True,
            "message": res.get("message", "Storage settings retrieved."),
            "result": res["result"],
        }

    # No row yet → ensure base row via update_system_storage_settings()
    ensure = update_system_storage_settings(db, {"radio_system_id": radio_system_id})
    if not ensure.get("success"):
        return {
            "success": False,
            "message": ensure.get("message", "Failed to ensure storage settings row."),
            "result": {}
        }

    # Re-fetch
    res = get_system_storage_settings(db, radio_system_id=radio_system_id)
    if not res.get("success") or not res.get("result"):
        return {
            "success": False,
            "message": res.get("message", "Re-fetch failed after ensuring row."),
            "result": {}
        }

    return {
        "success": True,
        "message": "Storage settings retrieved.",
        "result": res["result"],
    }

# ======================================================================
# Incident Classification settings          GET | PATCH
# ======================================================================

@api_systems.route("/<int:radio_system_id>/incident_classification/settings", methods=["GET", "PATCH"])
@login_required
@csrf_protect
def systems_incident_classification_settings(radio_system_id: int):
    db = current_app.config["db"]

    def shape(row: dict) -> dict:
        return {
            "incident_classification_setting_id": row.get("incident_classification_setting_id"),
            "enabled": row.get("incident_classification_enabled", 0),
            "openai_api_key": row.get("openai_api_key"),
            "openai_model": row.get("openai_model"),
            "min_confidence": row.get("min_confidence"),
        }

    # ------------- GET -------------
    if request.method == "GET":
        # Optional but recommended: ensure a row exists for older systems
        ensure = ensure_system_incident_classification_settings(db, radio_system_id)
        if not ensure.get("success"):
            return jsonify(success=False, message=ensure.get("message", "Ensure failed."), result=None), 500

        raw_res = get_system_incident_classification_settings(db, radio_system_id=radio_system_id)
        if not raw_res.get("success") or not raw_res.get("result"):
            return jsonify(success=False, message=raw_res.get("message", "No settings found."), result=None), 404

        return jsonify(success=True, message="Settings retrieved successfully.", result=shape(raw_res["result"])), 200

    # ------------- PATCH -------------
    raw = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    raw = raw or {}

    alias_map = {
        "enabled": "incident_classification_enabled",
    }

    allowed = {
        "incident_classification_enabled",
        "openai_api_key",
        "openai_model",
        "min_confidence",
        "radio_system_id",
    }

    payload = {}
    for k, v in raw.items():
        key = alias_map.get(k, k)
        if key in allowed:
            payload[key] = v

    payload["radio_system_id"] = radio_system_id

    upd = update_system_incident_classification_settings(db, payload)
    if not upd.get("success"):
        return jsonify(success=False, message=upd.get("message", "Update failed."), result=None), 400

    # Re-fetch so PATCH returns the same structure as GET
    raw_res = get_system_incident_classification_settings(db, radio_system_id=radio_system_id)
    if not raw_res.get("success") or not raw_res.get("result"):
        return jsonify(success=True, message="Updated, but re-fetch failed.", result=None), 200

    return jsonify(success=True, message="Incident classification settings updated.", result=raw_res["result"]), 200

# ────────────────────────────────────────────────────────────────────
# Channel ensure/seed helpers
# ────────────────────────────────────────────────────────────────────

def _ensure_row_for_system(db, table: str, radio_system_id: int) -> None:
    """
    Best-effort: create a settings row for the system if missing.
    Ignores 'no such table' errors so it can safely run across partial schemas.
    """
    sql = (
        f"INSERT INTO {table} (radio_system_id) "
        f"SELECT ? WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE radio_system_id = ?)"
    )
    res = db.execute_commit(sql, (radio_system_id, radio_system_id), return_row_id=False)
    # tolerate missing tables
    if not res.get("success") and "no such table" in (res.get("message","").lower()):
        return

def _ensure_system_channel_rows(db, radio_system_id: int) -> None:
    """
    Ensure a row exists in each system-level channel table.
    Safe to call on every trigger create/patch (idempotent).
    """
    for table in (
            "radio_system_discord_settings",
            "radio_system_telegram_settings",
            "radio_system_make_settings",
            "radio_system_pushover_settings",
            "radio_system_email_settings",
            "radio_system_n8n_settings",
    ):
        _ensure_row_for_system(db, table, radio_system_id)

def _ensure_trigger_child_rows(db, alert_trigger_id: int) -> None:
    """
    Ensure per-trigger child rows that should always exist.
    (Right now only Pushover is per-trigger; keep here for future channels.)
    """
    res = db.execute_commit(
        "INSERT INTO alert_trigger_pushover_settings (alert_trigger_id) "
        "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM alert_trigger_pushover_settings WHERE alert_trigger_id = ?)",
        (alert_trigger_id, alert_trigger_id),
        return_row_id=False,
    )
    # Silently ignore any errors; this runs defensively during PATCH too.
    _ = res

# ────────────────────────────────────────────────────────────────────
# Optional: seed default field payloads if none exist yet
# ────────────────────────────────────────────────────────────────────

def _seed_default_discord_fields_if_empty(db, radio_system_id: int) -> None:
    """
    If the system has zero Discord embed fields, seed a small sensible default set.
    Uses your existing helpers to fetch/add.
    """
    try:
        cur = _fetch_discord_fields_for_system(db, radio_system_id)
        if not cur.get("success"):
            return
        if cur.get("result"):  # already has fields
            return

        # Minimal, readable defaults
        defaults = [
            {"field_key": "system",   "field_label": "System",    "field_template": "{{ system_name }}",     "field_inline": 1, "field_enabled": 1},
            {"field_key": "tg",       "field_label": "Talkgroup", "field_template": "{{ talkgroup_name }}",  "field_inline": 1, "field_enabled": 1},
            {"field_key": "when",     "field_label": "Time",      "field_template": "{{ ts_iso }}",          "field_inline": 1, "field_enabled": 1},
        ]
        for i, row in enumerate(defaults, start=1):
            add_or_update_system_discord_field(db, {
                "radio_system_id": radio_system_id,
                **row,
                "sort_order": i
            })
    except Exception:
        # Best-effort; never break the caller
        pass

def _seed_default_make_fields_if_empty(db, radio_system_id: int) -> None:
    """
    If the system has zero Make payload fields, seed a compact default set.
    """
    try:
        cur = _fetch_make_fields_for_system(db, radio_system_id)
        if not cur.get("success"):
            return
        if cur.get("result"):
            return

        defaults = [
            {"field_key": "event",       "field_value": "alertpage.trigger.match", "field_enabled": 1},
            {"field_key": "system_id",   "field_value": "{{ system_id }}",         "field_enabled": 1},
            {"field_key": "system_name", "field_value": "{{ system_name }}",       "field_enabled": 1},
            {"field_key": "talkgroup",   "field_value": "{{ talkgroup_name }}",    "field_enabled": 1},
            {"field_key": "ts",          "field_value": "{{ ts_iso }}",            "field_enabled": 1},
        ]
        for row in defaults:
            add_or_update_system_make_field(db, {
                "radio_system_id": radio_system_id,
                **row
            })
    except Exception:
        pass

def _seed_default_channel_fields(db, radio_system_id: int) -> None:
    """
    Call this after ensuring the system channel rows.
    Keeps things minimal and idempotent.
    """
    _seed_default_discord_fields_if_empty(db, radio_system_id)
    _seed_default_make_fields_if_empty(db, radio_system_id)

def _make_discord_test_data(radio_system_id: int) -> tuple[dict, list[dict], str, list[dict]]:
    """
    Returns: (payload, fired_triggers, transcript_text, transcript_segments)
    with radio_system_id + start_epoch_s updated to now.
    """
    now = int(time.time())

    from copy import deepcopy
    payload = deepcopy(alert_test_payload)
    payload["radio_system_id"] = radio_system_id
    payload["start_epoch_s"] = now

    # Keep the audio_url as-is so attachments have the best chance of working.
    # If you want it to "look consistent", you *can* rewrite it here,
    # but it'll probably 404 unless that exact file exists.

    fired = deepcopy(alert_test_fired_trigger)
    for t in fired:
        t["radio_system_id"] = radio_system_id
        t["last_fired_at"] = now

    transcribe = deepcopy(alert_test_transcribe)
    transcript_text = (transcribe.get("text") or "").strip()
    transcript_segments = transcribe.get("segments") or []

    return payload, fired, transcript_text, transcript_segments


@api_systems.route("/mute-state", methods=["GET"])
@login_required
def get_mute_state():
    db = current_app.config["db"]
    result = db.execute_query(
        "SELECT COALESCE(SUM(mute_notifications), 0) > 0 AS muted FROM radio_systems",
        fetch_mode="one",
    )
    muted = result["result"]["muted"] if result["success"] and result["result"] else False
    return jsonify({"muted": bool(muted)})


@api_systems.route("/toggle-mute", methods=["POST"])
@csrf_protect
@login_required
def toggle_mute():
    db = current_app.config["db"]

    # Permission check: non-admins can only mute their assigned systems
    user_is_admin = session.get("is_admin", False)
    user_systems = session.get("user_systems", {})

    result = db.execute_query(
        "SELECT COALESCE(SUM(mute_notifications), 0) > 0 AS muted FROM radio_systems",
        fetch_mode="one",
    )
    current_muted = result["result"]["muted"] if result["success"] and result["result"] else False
    new_muted = not current_muted

    if user_is_admin:
        db.execute_commit(
            "UPDATE radio_systems SET mute_notifications = ?",
            (1 if new_muted else 0,),
        )
    else:
        # Non-admin: only mute systems they have access to
        if not user_systems:
            return jsonify({"muted": current_muted, "message": "No systems assigned"}), 403
        sys_ids = tuple(user_systems.keys())
        placeholders = ",".join("?" for _ in sys_ids)
        db.execute_commit(
            f"UPDATE radio_systems SET mute_notifications = ? WHERE radio_system_id IN ({placeholders})",
            (1 if new_muted else 0,) + sys_ids,
        )

    message = "Notifications muted" if new_muted else "Notifications resumed"
    return jsonify({"muted": new_muted, "message": message})


@api_systems.route("/stats", methods=["GET"])
@login_required
def get_stats():
    db = current_app.config["db"]
    today = datetime.date.today()
    today_start = datetime.datetime.combine(today, datetime.time(0, 0, 0)).timestamp()

    total_calls_today = 0
    calls_with_tones = 0
    triggers_fired = 0

    try:
        result = db.execute_query(
            "SELECT COUNT(*) AS total FROM call_records WHERE start_epoch_s >= ?",
            (today_start,),
            fetch_mode="one",
        )
        total_calls_today = result["result"]["total"] if result["success"] and result["result"] else 0

        result = db.execute_query(
            "SELECT COUNT(DISTINCT call_id) AS total FROM call_tone_events WHERE call_id IN (SELECT call_id FROM call_records WHERE start_epoch_s >= ?)",
            (today_start,),
            fetch_mode="one",
        )
        calls_with_tones = result["result"]["total"] if result["success"] and result["result"] else 0

        result = db.execute_query(
            "SELECT COUNT(DISTINCT call_id) AS total FROM trigger_fires WHERE call_id IN (SELECT call_id FROM call_records WHERE start_epoch_s >= ?)",
            (today_start,),
            fetch_mode="one",
        )
        triggers_fired = result["result"]["total"] if result["success"] and result["result"] else 0
    except Exception:
        pass

    result = db.execute_query(
        "SELECT COUNT(*) AS total FROM radio_systems",
        fetch_mode="one",
    )
    active_systems = result["result"]["total"] if result["success"] and result["result"] else 0

    return jsonify({
        "total_calls_today": total_calls_today,
        "calls_with_tones": calls_with_tones,
        "triggers_fired_today": triggers_fired,
        "active_systems": active_systems,
    })
