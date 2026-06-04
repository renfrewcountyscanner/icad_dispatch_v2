"""
alert_trigger_module.py

CRUD helpers for alert triggers and their per-trigger condition sets (new format).

This module ONLY uses the multi-set child tables:
  - alert_trigger_two_tone_sets
  - alert_trigger_long_tone_sets
  - alert_trigger_hi_low_sets
  - alert_trigger_pulsed_sets
  - alert_trigger_dtmf_sequences

Legacy flat tone columns on `alert_triggers` are intentionally ignored here.

Public API:
    - get_triggers(db, **filters)
    - get_triggers_full(db, **filters)
    - get_pushover_settings(db, alert_trigger_id)
    - add_trigger(db, trigger_data)
    - patch_trigger(db, trigger_id, patch)
    - patch_pushover(db, trigger_id, patch)
    - delete_trigger(db, trigger_id)
"""

from __future__ import annotations
from typing import List, Any, Iterable, Optional, Tuple, Dict
import os, binascii

# ────────────────────────────────────────────────────────────────
# Main-table columns we allow reading/writing (metadata only)
# ────────────────────────────────────────────────────────────────
MAIN_TRIGGER_COLS: set[str] = {
    "alert_trigger_id",
    "radio_system_id",
    "alert_trigger_name",
    "alert_trigger_type",          # 'AND' | 'OR'
    "alert_trigger_tone_tolerance",# optional global default; per-rule tol_pct preferred
    "alert_trigger_ignore_time",   # throttle window (seconds)
    "alert_trigger_talkgroup",

    "alert_trigger_stream_url",
    "alert_trigger_enable_discord",
    "alert_trigger_enable_make",
    "alert_trigger_enable_telegram",
    "alert_trigger_enable_ntfy",
    "alert_trigger_enabled",
}

_INT_COLS_MAIN = {
    "alert_trigger_talkgroup",
    "alert_trigger_enable_discord",
    "alert_trigger_enable_make",
    "alert_trigger_enable_telegram",
    "alert_trigger_enable_ntfy",
    "alert_trigger_enabled",
}

_FLOAT_COLS_MAIN = {
    "alert_trigger_tone_tolerance",
    "alert_trigger_ignore_time",
}

READONLY_MAIN = {"alert_trigger_id"}

# columns for the 1:1 pushover child table
PUSHOVER_COLUMNS = [
    "enable_pushover",
    "pushover_group_token",
    "pushover_app_token",
    "pushover_body",
    "pushover_subject",
    "pushover_sound",
]

# payload keys for child sets
_CHILD_KEYS = {
    "two_tone_sets",
    "long_tone_sets",
    "hi_low_sets",
    "pulsed_sets",
    "dtmf_sequences",
}

# ────────────────────────────────────────────────────────────────
# Public: SELECT helpers
# ────────────────────────────────────────────────────────────────

def get_triggers(
        db,
        *,
        radio_system_id: List[int] | int | None = None,
        alert_trigger_id: List[int] | int | None = None,
        alert_trigger_name: List[str] | str | None = None,
        alert_trigger_talkgroup: List[int] | int | None = None,
) -> dict:
    """
    Return rows from `alert_triggers` (main table only, no child sets).

    Args:
        db: Database wrapper exposing execute_query/execute_commit.
        radio_system_id: One or many system ids to filter.
        alert_trigger_id: One or many trigger ids to filter.
        alert_trigger_name: One or many names to filter (exact match).
        alert_trigger_talkgroup: One or many TGs to filter.

    Returns:
        dict: {"success": bool, "message": str, "result": list[dict]}
    """
    wh, params = [], []

    def _where(col: str, vals: Iterable[Any]):
        vals = list(vals)
        if not vals:
            return
        placeholders = ",".join("?" * len(vals))
        wh.append(f"{col} IN ({placeholders})")
        params.extend(vals)

    if radio_system_id is not None:
        _where("radio_system_id", radio_system_id if isinstance(radio_system_id, list) else [radio_system_id])
    if alert_trigger_id is not None:
        _where("alert_trigger_id", alert_trigger_id if isinstance(alert_trigger_id, list) else [alert_trigger_id])
    if alert_trigger_name is not None:
        _where("alert_trigger_name", alert_trigger_name if isinstance(alert_trigger_name, list) else [alert_trigger_name])
    if alert_trigger_talkgroup is not None:
        _where("alert_trigger_talkgroup", alert_trigger_talkgroup if isinstance(alert_trigger_talkgroup, list) else [alert_trigger_talkgroup])

    sql = "SELECT * FROM alert_triggers"
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY LOWER(alert_trigger_name)"

    return db.execute_query(sql, params, fetch_mode="all")


def get_triggers_full(
        db,
        *,
        radio_system_id: List[int] | int | None = None,
        alert_trigger_id: List[int] | int | None = None,
        alert_trigger_name: List[str] | str | None = None,
        alert_trigger_talkgroup: List[int] | int | None = None,
) -> dict:
    """
    Return triggers including their child condition sets.

    Child arrays added to each row:
      - two_tone_sets
      - long_tone_sets
      - hi_low_sets
      - pulsed_sets
      - dtmf_sequences
    """
    base = get_triggers(
        db,
        radio_system_id=radio_system_id,
        alert_trigger_id=alert_trigger_id,
        alert_trigger_name=alert_trigger_name,
        alert_trigger_talkgroup=alert_trigger_talkgroup,
    )
    if not base.get("success"):
        return base

    rows = base.get("result") or []
    for r in rows:
        tid = r["alert_trigger_id"]
        r["two_tone_sets"]   = _fetch_two_tone_sets(db, tid)
        r["long_tone_sets"]  = _fetch_long_tone_sets(db, tid)
        r["hi_low_sets"]     = _fetch_hi_low_sets(db, tid)
        r["pulsed_sets"]     = _fetch_pulsed_sets(db, tid)
        r["dtmf_sequences"]  = _fetch_dtmf_sequences(db, tid)
    return {"success": True, "message": "OK", "result": rows}


def get_pushover_settings(db, alert_trigger_id: int) -> dict:
    """
    Return the pushover settings row for a trigger (1:1 child table).
    """
    sql = "SELECT * FROM alert_trigger_pushover_settings WHERE alert_trigger_id=?"
    return db.execute_query(sql, (alert_trigger_id,), fetch_mode="one")

# ────────────────────────────────────────────────────────────────
# Public: INSERT / UPDATE / DELETE
# ────────────────────────────────────────────────────────────────

def add_trigger(db, trigger_data: dict) -> dict:
    """
    Insert a new alert trigger.

    Accepted payload keys:
      - Main-table metadata: subset of MAIN_TRIGGER_COLS (radio_system_id required)
      - Child arrays: two_tone_sets, long_tone_sets, hi_low_sets, pulsed_sets, dtmf_sequences
        Each array may use either DB-shaped keys or friendly short keys. See upsert_* normalizers.

    All writes occur in one transaction. Pushover child row is auto-created.
    """
    if not isinstance(trigger_data, dict):
        return {"success": False, "message": "Payload must be a JSON object", "result": []}

    main_data = _sanitize_main_create(trigger_data)
    if "radio_system_id" not in main_data or main_data["radio_system_id"] is None:
        return {"success": False, "message": "radio_system_id is required", "result": []}

    sets_payload = {k: trigger_data.get(k) for k in _CHILD_KEYS if k in trigger_data}

    cols, vals, q = [], [], []
    for k, v in main_data.items():
        if k in READONLY_MAIN:
            continue
        cols.append(k); vals.append(v); q.append("?")

    _tx_begin(db)
    try:
        ins = db.execute_commit(
            f"INSERT INTO alert_triggers ({','.join(cols)}) VALUES ({','.join(q)})",
            vals,
            return_row_id=True,
        )
        if not ins.get("success"):
            _tx_rollback(db)
            return ins

        trig_id = ins["result"]

        # ensure pushover child exists
        ok = db.execute_commit(
            "INSERT INTO alert_trigger_pushover_settings (alert_trigger_id) VALUES (?)",
            (trig_id,),
            return_row_id=False,
        )
        if not ok.get("success"):
            _tx_rollback(db)
            return ok

        # apply child sets (UPSERT by rule_uid)
        if sets_payload:
            _apply_sets_payload(db, trig_id, sets_payload)

        _tx_commit(db)
        return {"success": True, "message": "Trigger created", "result": trig_id}
    except Exception as e:
        _tx_rollback(db)
        return {"success": False, "message": f"add_trigger failed: {e}", "result": []}


def patch_trigger(db, trigger_id: int, patch: dict) -> dict:
    """
    Patch an existing alert trigger.

    Accepts metadata fields and/or child arrays. For each child array present,
    rows are upserted by `rule_uid`; rows not present in that array payload
    are deleted for that category (orphan pruning) so the table matches the client.
    """
    if not isinstance(patch, dict):
        return {"success": False, "message": "Payload must be a JSON object", "result": []}

    sets_payload = {k: patch.get(k) for k in _CHILD_KEYS if k in patch}
    main_patch   = _sanitize_main_patch(patch)

    _tx_begin(db)
    try:
        if main_patch:
            sets, params = [], []
            for k, v in main_patch.items():
                if k in READONLY_MAIN:
                    continue
                sets.append(f"{k}=?")
                params.append(v)
            if sets:
                params.append(trigger_id)
                sql = f"UPDATE alert_triggers SET {', '.join(sets)} WHERE alert_trigger_id=?"
                up = db.execute_commit(sql, params, return_row_id=False, return_count=True)
                if not up.get("success"):
                    _tx_rollback(db)
                    return up

        if sets_payload:
            _apply_sets_payload(db, trigger_id, sets_payload)

        _tx_commit(db)
        return {"success": True, "message": "Trigger updated", "result": []}
    except Exception as e:
        _tx_rollback(db)
        return {"success": False, "message": f"patch_trigger failed: {e}", "result": []}


def patch_pushover(db, trigger_id: int, patch: dict) -> dict:
    """
    Patch the 1:1 pushover settings row for a trigger.
    """
    if not isinstance(patch, dict):
        return {"success": False, "message": "Payload must be a JSON object", "result": []}

    sets, params = [], []
    for k, v in patch.items():
        if k in PUSHOVER_COLUMNS:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return {"success": True, "message": "Nothing to update", "result": []}

    params.append(trigger_id)
    sql = f"UPDATE alert_trigger_pushover_settings SET {', '.join(sets)} WHERE alert_trigger_id=?"
    return db.execute_commit(sql, params, return_row_id=False, return_count=True)


def delete_trigger(db, trigger_id: int) -> dict:
    """
    Delete a trigger. Child rows are deleted by FK CASCADE.
    """
    return db.execute_commit(
        "DELETE FROM alert_triggers WHERE alert_trigger_id=?",
        (trigger_id,),
        return_row_id=False,
        return_count=True,
    )

# ────────────────────────────────────────────────────────────────
# Internal: child-set fetchers
# ────────────────────────────────────────────────────────────────

def _fetch_two_tone_sets(db, trigger_id: int) -> list[dict]:
    sql = """SELECT two_tone_set_id, alert_trigger_id, rule_uid,
                    freq_a_hz, min_len_a_s, freq_b_hz, min_len_b_s,
                    tol_pct, sort_order
             FROM alert_trigger_two_tone_sets
             WHERE alert_trigger_id=? ORDER BY sort_order, two_tone_set_id"""
    res = db.execute_query(sql, (trigger_id,), fetch_mode="all")
    return res.get("result", []) if res.get("success") else []

def _fetch_long_tone_sets(db, trigger_id: int) -> list[dict]:
    sql = """SELECT long_tone_set_id, alert_trigger_id, rule_uid,
                    freq_hz, min_len_s, tol_pct, sort_order
             FROM alert_trigger_long_tone_sets
             WHERE alert_trigger_id=? ORDER BY sort_order, long_tone_set_id"""
    res = db.execute_query(sql, (trigger_id,), fetch_mode="all")
    return res.get("result", []) if res.get("success") else []

def _fetch_hi_low_sets(db, trigger_id: int) -> list[dict]:
    sql = """SELECT hi_low_set_id, alert_trigger_id, rule_uid,
                    hi_freq_a_hz, hi_freq_b_hz, min_alternations, interval_s,
                    tol_pct, sort_order
             FROM alert_trigger_hi_low_sets
             WHERE alert_trigger_id=? ORDER BY sort_order, hi_low_set_id"""
    res = db.execute_query(sql, (trigger_id,), fetch_mode="all")
    return res.get("result", []) if res.get("success") else []

def _fetch_pulsed_sets(db, trigger_id: int) -> list[dict]:
    sql = """SELECT pulsed_set_id, alert_trigger_id, rule_uid,
                    center_hz, min_cycles, min_on_ms, max_on_ms,
                    min_off_ms, max_off_ms, tol_pct, sort_order
             FROM alert_trigger_pulsed_sets
             WHERE alert_trigger_id=? ORDER BY sort_order, pulsed_set_id"""
    res = db.execute_query(sql, (trigger_id,), fetch_mode="all")
    return res.get("result", []) if res.get("success") else []

def _fetch_dtmf_sequences(db, trigger_id: int) -> list[dict]:
    sql = """SELECT dtmf_set_id, alert_trigger_id, rule_uid,
                    sequence, sort_order
             FROM alert_trigger_dtmf_sequences
             WHERE alert_trigger_id=? ORDER BY sort_order, dtmf_set_id"""
    res = db.execute_query(sql, (trigger_id,), fetch_mode="all")
    return res.get("result", []) if res.get("success") else []

# ────────────────────────────────────────────────────────────────
# Internal: child-set upserts (by rule_uid) + payload apply
# ────────────────────────────────────────────────────────────────

def _apply_sets_payload(db, trigger_id: int, sets_payload: dict) -> None:
    """
    Apply child-set arrays for a trigger using UPSERT-by-rule_uid and
    orphan pruning (delete rows not present in the payload for a category).
    """
    if "two_tone_sets" in sets_payload:
        upsert_two_tone_sets(db, trigger_id, sets_payload["two_tone_sets"] or [])
    if "long_tone_sets" in sets_payload:
        upsert_long_tone_sets(db, trigger_id, sets_payload["long_tone_sets"] or [])
    if "hi_low_sets" in sets_payload:
        upsert_hi_low_sets(db, trigger_id, sets_payload["hi_low_sets"] or [])
    if "pulsed_sets" in sets_payload:
        upsert_pulsed_sets(db, trigger_id, sets_payload["pulsed_sets"] or [])
    if "dtmf_sequences" in sets_payload:
        upsert_dtmf_sequences(db, trigger_id, sets_payload["dtmf_sequences"] or [])


def upsert_two_tone_sets(db, trigger_id: int, items: list[dict]) -> None:
    """
    Accepts either DB keys {freq_a_hz,min_len_a_s,freq_b_hz,min_len_b_s,tol_pct,sort_order}
    or friendly keys {a,a_len,b,b_len,tol,sort_order}.
    """
    def norm(it, i):
        a     = _parse_to_float(it.get("freq_a_hz", it.get("a")))
        a_len = _parse_to_float(it.get("min_len_a_s", it.get("a_len")))
        b     = _parse_to_float(it.get("freq_b_hz", it.get("b")))
        b_len = _parse_to_float(it.get("min_len_b_s", it.get("b_len")))
        tol   = _parse_to_float(it.get("tol_pct", it.get("tol")))
        return {
            "rule_uid": _rule_uid(it),
            "freq_a_hz": a,
            "min_len_a_s": a_len,
            "freq_b_hz": b,
            "min_len_b_s": b_len,
            "tol_pct": tol,
            "sort_order": _parse_to_int(it.get("sort_order", i)),
        }
    _sync_child_table(
        db,
        table="alert_trigger_two_tone_sets",
        trigger_id=trigger_id,
        items=items,
        key_cols=["rule_uid"],
        updatable_cols=["freq_a_hz","min_len_a_s","freq_b_hz","min_len_b_s","tol_pct","sort_order"],
        normalize_item=norm,
    )


def upsert_long_tone_sets(db, trigger_id: int, items: list[dict]) -> None:
    """
    Accepts DB keys {freq_hz,min_len_s,tol_pct,sort_order} or friendly {f,min_len,tol,sort_order}.
    """
    def norm(it, i):
        f       = _parse_to_float(it.get("freq_hz", it.get("f")))
        min_len = _parse_to_float(it.get("min_len_s", it.get("min_len")))
        tol     = _parse_to_float(it.get("tol_pct", it.get("tol")))
        return {
            "rule_uid": _rule_uid(it),
            "freq_hz": f,
            "min_len_s": min_len,
            "tol_pct": tol,
            "sort_order": _parse_to_int(it.get("sort_order", i)),
        }
    _sync_child_table(
        db,
        table="alert_trigger_long_tone_sets",
        trigger_id=trigger_id,
        items=items,
        key_cols=["rule_uid"],
        updatable_cols=["freq_hz","min_len_s","tol_pct","sort_order"],
        normalize_item=norm,
    )


def upsert_hi_low_sets(db, trigger_id: int, items: list[dict]) -> None:
    """
    Accepts DB keys {hi_freq_a_hz,hi_freq_b_hz,min_alternations,interval_s,tol_pct}
    or friendly {a,b,alternations,interval,tol}.
    """
    def norm(it, i):
        a   = _parse_to_float(it.get("hi_freq_a_hz", it.get("a")))
        b   = _parse_to_float(it.get("hi_freq_b_hz", it.get("b")))
        alt = _parse_to_int(it.get("min_alternations", it.get("alternations", 4)))
        interval = _parse_to_float(it.get("interval_s", it.get("interval")))
        tol = _parse_to_float(it.get("tol_pct", it.get("tol")))
        return {
            "rule_uid": _rule_uid(it),
            "hi_freq_a_hz": a,
            "hi_freq_b_hz": b,
            "min_alternations": alt,
            "interval_s": interval,
            "tol_pct": tol,
            "sort_order": _parse_to_int(it.get("sort_order", i)),
        }
    _sync_child_table(
        db,
        table="alert_trigger_hi_low_sets",
        trigger_id=trigger_id,
        items=items,
        key_cols=["rule_uid"],
        updatable_cols=["hi_freq_a_hz","hi_freq_b_hz","min_alternations","interval_s","tol_pct","sort_order"],
        normalize_item=norm,
    )


def upsert_pulsed_sets(db, trigger_id: int, items: list[dict]) -> None:
    """
    Accepts DB keys {center_hz,min_cycles,min_on_ms,max_on_ms,min_off_ms,max_off_ms,tol_pct}
    or friendly {f,min_cycles,on_ms,off_ms,tol}.
      - Friendly `on_ms` maps to both min_on_ms and max_on_ms only if caller provided
        explicit min/max; otherwise leave individual fields as provided by caller.
    """
    def norm(it, i):
        f     = _parse_to_float(it.get("center_hz", it.get("f")))
        cyc   = _parse_to_int(it.get("min_cycles"))
        on_ms  = it.get("min_on_ms", it.get("on_ms"))
        off_ms = it.get("min_off_ms", it.get("off_ms"))
        # Accept explicit ranges if present
        min_on  = _parse_to_int(on_ms)
        max_on  = _parse_to_int(it.get("max_on_ms"))
        min_off = _parse_to_int(off_ms)
        max_off = _parse_to_int(it.get("max_off_ms"))
        tol = _parse_to_float(it.get("tol_pct", it.get("tol")))
        return {
            "rule_uid": _rule_uid(it),
            "center_hz": f,
            "min_cycles": cyc if cyc is not None else 6,
            "min_on_ms": min_on,
            "max_on_ms": max_on,
            "min_off_ms": min_off,
            "max_off_ms": max_off,
            "tol_pct": tol,
            "sort_order": _parse_to_int(it.get("sort_order", i)),
        }
    _sync_child_table(
        db,
        table="alert_trigger_pulsed_sets",
        trigger_id=trigger_id,
        items=items,
        key_cols=["rule_uid"],
        updatable_cols=[
            "center_hz","min_cycles","min_on_ms","max_on_ms",
            "min_off_ms","max_off_ms","tol_pct","sort_order"
        ],
        normalize_item=norm,
    )


def upsert_dtmf_sequences(db, trigger_id: int, items: list[dict]) -> None:
    """

    Accepts ONLY:
      - sequence   (required)  -> SQLite LIKE pattern (supports % and _)
      - rule_uid   (optional)
      - sort_order (optional)
    """
    def norm(it, i):

        seq = it.get("sequence")
        seq = _norm_str(seq)
        if not seq:
            raise ValueError("dtmf_sequences[].sequence is required")

        seq = seq.upper()

        # Validate allowed chars: DTMF digits + A-D + * # plus LIKE wildcards % _
        allowed = set("0123456789ABCD*#%_")
        bad_chars = sorted({c for c in seq if c not in allowed})
        if bad_chars:
            raise ValueError(
                f"dtmf_sequences[].sequence has invalid characters {bad_chars}. "
                f"Allowed: 0-9, A-D, *, #, %, _"
            )

        return {
            "rule_uid": _rule_uid(it),
            "sequence": seq,
            "sort_order": _parse_to_int(it.get("sort_order", i)),
        }

    _sync_child_table(
        db,
        table="alert_trigger_dtmf_sequences",
        trigger_id=trigger_id,
        items=items,
        key_cols=["rule_uid"],
        updatable_cols=["sequence", "sort_order"],
        normalize_item=norm,
    )

# ────────────────────────────────────────────────────────────────
# Internal: generic child-table sync helper (UPSERT + prune)
# ────────────────────────────────────────────────────────────────

def _sync_child_table(
        db,
        *,
        table: str,
        trigger_id: int,
        items: list[dict],
        key_cols: list[str],
        updatable_cols: list[str],
        normalize_item,
        delete_orphans: bool = True,
) -> None:
    """
    Idempotently synchronize a child table for one trigger_id.

    - Normalizes each incoming item (adds alert_trigger_id and rule_uid).
    - UPSERTs on UNIQUE(alert_trigger_id, rule_uid) using SQLite ON CONFLICT.
    - Optionally deletes rows not present in the incoming payload.

    Requires the target table to have UNIQUE(alert_trigger_id, rule_uid).
    """
    normalized: list[dict] = []
    for i, raw in enumerate(items or []):
        raw = raw or {}
        it = normalize_item(raw, i)
        it["alert_trigger_id"] = trigger_id
        normalized.append(it)

    # Orphan pruning (by key tuple); simple per-row deletes
    if delete_orphans:
        existing = _fetch_existing_keys(db, table, trigger_id, key_cols)
        incoming = set(_make_key_tuple(it, key_cols) for it in normalized)
        to_delete = existing - incoming
        if to_delete:
            where = " AND ".join([f"{c}=?" for c in key_cols])
            del_sql = f"DELETE FROM {table} WHERE alert_trigger_id=? AND {where}"
            for key_tuple in to_delete:
                db.execute_commit(del_sql, (trigger_id, *key_tuple))

    if not normalized:
        return

    insert_cols = ["alert_trigger_id"] + key_cols + updatable_cols
    placeholders = ",".join("?" for _ in insert_cols)
    conflict_cols = ", ".join(["alert_trigger_id", *key_cols])
    set_clause = ", ".join([f"{c}=COALESCE(excluded.{c}, {c})" for c in updatable_cols])

    upsert_sql = (
        f"INSERT INTO {table} ({', '.join(insert_cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clause}"
    )

    for it in normalized:
        params = [it.get(c) for c in insert_cols]
        db.execute_commit(upsert_sql, params)


def _fetch_existing_keys(db, table: str, trigger_id: int, key_cols: list[str]) -> set[tuple]:
    cols = ", ".join(key_cols)
    sql = f"SELECT {cols} FROM {table} WHERE alert_trigger_id=?"
    res = db.execute_query(sql, (trigger_id,), fetch_mode="all")
    if not res.get("success"):
        return set()
    keys = set()
    for row in res.get("result", []) or []:
        keys.add(tuple(row[c] for c in key_cols))
    return keys


def _make_key_tuple(item: dict, key_cols: list[str]) -> tuple:
    return tuple(item.get(c) for c in key_cols)

# ────────────────────────────────────────────────────────────────
# Internal: main-table sanitizers
# ────────────────────────────────────────────────────────────────

def _sanitize_main_create(d: dict) -> dict:
    """
    Filter to MAIN_TRIGGER_COLS and coerce types for INSERT.
    """
    out: dict = {}
    for k, v in d.items():
        if k not in MAIN_TRIGGER_COLS or k in READONLY_MAIN:
            continue
        out[k] = _coerce_main_value(k, v)
    # Normalize type
    if "alert_trigger_type" in out and isinstance(out["alert_trigger_type"], str):
        t = out["alert_trigger_type"].strip().upper()
        out["alert_trigger_type"] = "OR" if t == "OR" else "AND"
    return out


def _sanitize_main_patch(d: dict) -> dict:
    """
    Filter to MAIN_TRIGGER_COLS and coerce types for UPDATE.
    """
    out: dict = {}
    for k, v in d.items():
        if k not in MAIN_TRIGGER_COLS:
            continue
        if k in READONLY_MAIN:
            continue
        out[k] = _coerce_main_value(k, v)
    if "alert_trigger_type" in out and isinstance(out["alert_trigger_type"], str):
        t = out["alert_trigger_type"].strip().upper()
        out["alert_trigger_type"] = "OR" if t == "OR" else "AND"
    return out


def _coerce_main_value(k: str, v):
    if v == "":
        return None
    if k in _INT_COLS_MAIN and v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if k in _FLOAT_COLS_MAIN and v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return v

# ────────────────────────────────────────────────────────────────
# Internal: small utilities
# ────────────────────────────────────────────────────────────────

def _parse_to_float(val):
    """Return float(val) or None if it can’t be coerced."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_to_int(val):
    """Return int(val) or None if it can’t be coerced."""
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _norm_str(x):
    """Trim to str or None."""
    return None if x is None else str(x).strip()


def _rule_uid(it: dict) -> str:
    """
    Ensure a stable rule_uid. If the client did not supply one,
    generate a random uid. The client should persist this id and
    send it back on future edits to allow UPSERT instead of insert.
    """
    uid = it.get("rule_uid")
    if isinstance(uid, str) and uid.strip():
        return uid.strip()
    return binascii.hexlify(os.urandom(8)).decode("ascii")


def _tx_begin(db) -> None:
    """
    Begin a transaction if the wrapper exposes begin(); otherwise run BEGIN.
    Safe even if the wrapper auto-begins per connection.
    """
    if hasattr(db, "begin") and callable(getattr(db, "begin")):
        db.begin()
    else:
        db.execute_commit("BEGIN", (), return_row_id=False)


def _tx_commit(db) -> None:
    """
    Commit a transaction; falls back to COMMIT if no method exists.
    """
    if hasattr(db, "commit") and callable(getattr(db, "commit")):
        db.commit()
    else:
        db.execute_commit("COMMIT", (), return_row_id=False)


def _tx_rollback(db) -> None:
    """
    Roll back a transaction; falls back to ROLLBACK if no method exists.
    """
    if hasattr(db, "rollback") and callable(getattr(db, "rollback")):
        db.rollback()
    else:
        db.execute_commit("ROLLBACK", (), return_row_id=False)
