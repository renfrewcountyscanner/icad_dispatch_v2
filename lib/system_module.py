import logging
import re
from typing import Optional, Any, Dict, List, Tuple
from pathlib import Path

from lib.sqlite_module import SQLiteDatabase
from lib.utility import _generate_api_key, _norm_str

module_logger = logging.getLogger("icad_dispatch.system_handler")

SSH_KEY_ROOT = Path("var/.ssh")
_APIKEY_RE = re.compile(r"^[A-Za-z0-9_\-\.~]{16,128}$")

# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------
def _bool_like(v: Any) -> int:
    """Coerce diverse truthy/falsy inputs into 1/0."""
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v else 0
    if isinstance(v, str):
        return 1 if v.strip().lower() in {"1", "true", "yes", "on"} else 0
    return 0


def _in_clause(col: str, values: List[Any]) -> Tuple[str, List[Any]]:
    """
    Build a SQL IN (...) clause for SQLite with '?' placeholders.

    Example:
        _in_clause("rs.radio_system_id", [1,2,3])
        -> ("rs.radio_system_id IN (?,?,?)", [1,2,3])
    """
    placeholders = ",".join("?" for _ in values)
    return f"{col} IN ({placeholders})", values


# ===========================================================================
# System lookups / CRUD
# ===========================================================================
def check_for_system(
        db: SQLiteDatabase,
        *,
        system_name: Optional[str] = None,
        system_decimal: Optional[int] = None,
        radio_system_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Return a single **radio_systems** record that matches the first non-null
    identifier supplied, with the following priority:

    1. radio_system_id (PK)
    2. system_decimal  (unique)
    3. system_name     (exact match, case-sensitive)

    Parameters
    ----------
    db : SQLiteDatabase
        Database wrapper instance.
    system_name : str | None
        System name to match exactly.
    system_decimal : int | None
        Unique decimal identifier.
    radio_system_id : int | None
        Primary key.

    Returns
    -------
    dict
        Row dict if found, else {}.

    Raises
    ------
    ValueError
        If all identifiers are None.
    """
    if radio_system_id is not None:
        q = "SELECT * FROM radio_systems WHERE radio_system_id = ?"
        p = (radio_system_id,)
    elif system_decimal is not None:
        q = "SELECT * FROM radio_systems WHERE system_decimal = ?"
        p = (system_decimal,)
    elif system_name is not None:
        q = "SELECT * FROM radio_systems WHERE system_name = ?"
        p = (system_name,)
    else:
        raise ValueError("Must provide radio_system_id, system_decimal, or system_name.")

    res = db.execute_query(q, p, fetch_mode="one")
    return res.get("result", {}) if res.get("success") else {}


def add_system(db: SQLiteDatabase, system_data: dict) -> dict:
    """
    Create a new radio system **and** all default child rows in a single transaction.

    Expected keys in `system_data`:
      - system_decimal (int, required)
      - system_name    (str, required)
      - stream_url     (str|None, optional)

    Returns
    -------
    dict
      {
        "success": bool,
        "message": str,
        "result": radio_system_id | None
      }
    """
    # 1) validation
    if not system_data.get("system_decimal"):
        return {"success": False, "message": "System decimal field is required", "result": None}
    if not system_data.get("system_name"):
        return {"success": False, "message": "System name field is required", "result": None}

    # uniqueness checks (cheap indexed lookups)
    if check_for_system(db, system_decimal=system_data["system_decimal"]):
        return {"success": False, "message": "System decimal already exists", "result": None}
    if check_for_system(db, system_name=system_data["system_name"]):
        return {"success": False, "message": "System name already exists", "result": None}

    api_key = _generate_api_key()

    # 2) atomic create
    try:
        db.begin()

        parent = db.execute_commit(
            """
            INSERT INTO radio_systems (system_decimal, system_name, stream_url, api_key)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(system_data["system_decimal"]),
                system_data["system_name"],
                system_data.get("stream_url"),
                api_key,
            ),
            return_row_id=True,
        )
        if not parent["success"]:
            raise RuntimeError(parent["message"])

        radio_system_id = int(parent["result"])

        add_system_default_settings(db, radio_system_id)

        db.commit()
        return {
            "success": True,
            "message": f'New system "{system_data["system_name"]}" added.',
            "result": radio_system_id,
        }

    except Exception as e:
        module_logger.error("add_system() rolled back: %s", e)
        db.rollback()
        return {"success": False, "message": str(e), "result": None}


def add_system_default_settings(db: SQLiteDatabase, radio_system_id: int) -> None:
    """
    Insert the default child rows for a new radio system.

    The caller **must already be inside a transaction**.
    Raises RuntimeError on first failure, so the parent transaction can roll back.
    """
    # 1) upload / split-dispatch
    ok = db.execute_commit(
        "INSERT INTO radio_system_upload_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"upload settings: {ok['message']}")

    # 2) tone settings
    ok = db.execute_commit(
        "INSERT INTO radio_system_tone_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"tone settings: {ok['message']}")

    # 3) email settings (with default template)
    ok = db.execute_commit(
        """
        INSERT INTO radio_system_email_settings
            (radio_system_id, email_alert_body)
        VALUES (?, ?)
        """,
        (
            radio_system_id,
            "{trigger_list} Alert at {timestamp_24}<br><br>{transcript}"
            '<br><br><a href="{audio_url}">Click for Dispatch Audio</a>'
            '<br><br><a href="{stream_url}">Click Audio Stream</a>',
        ),
    )
    if not ok["success"]:
        raise RuntimeError(f"email settings: {ok['message']}")

    # 4) pushover settings
    ok = db.execute_commit(
        """
        INSERT INTO radio_system_pushover_settings
            (radio_system_id, pushover_body)
        VALUES (?, ?)
        """,
        (
            radio_system_id,
            '<font color="red"><b>{trigger_list}</b></font><br><br>'
            '<a href="{audio_url}">Click for Dispatch Audio</a>'
            '<br><br><a href="{stream_url}">Click Audio Stream</a>',
        ),
    )
    if not ok["success"]:
        raise RuntimeError(f"pushover settings: {ok['message']}")

    # 5) telegram
    ok = db.execute_commit(
        "INSERT INTO radio_system_telegram_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"telegram settings: {ok['message']}")

    # 6) discord (base row)
    discord_res = db.execute_commit(
        """
        INSERT INTO radio_system_discord_settings
        (radio_system_id, discord_enabled, discord_embed_title, discord_embed_color, discord_embed_footer)
        VALUES (?, 0, '🚨 Dispatch Alert', '#F04646', '{timestamp_24}')
        """,
        (radio_system_id,),
        return_row_id=True,
    )
    if not discord_res["success"]:
        raise RuntimeError(f"discord settings: {discord_res['message']}")

    discord_setting_id = int(discord_res["result"])

    #    default embed fields
    default_fields = [
        (10, "departments", "Departments", "{trigger_list}", 0),
        (20, "transcript", "Transcript", "{transcript}", 0),
        (30, "audio", "Dispatch Audio", "{audio_url}", 0),
    ]
    for sort, key, label, tpl, inline in default_fields:
        ok = db.execute_commit(
            """
            INSERT INTO radio_system_discord_embed_fields
            (discord_setting_id, field_key, field_label, field_template, field_inline, field_enabled, sort_order)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (discord_setting_id, key, label, tpl, inline, sort),
        )
        if not ok["success"]:
            raise RuntimeError(f"discord field '{key}': {ok['message']}")

    # 7) make.com settings + starter keys
    make_res = db.execute_commit(
        "INSERT INTO radio_system_make_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
        return_row_id=True,
    )
    if not make_res["success"]:
        raise RuntimeError(f"make settings: {make_res['message']}")
    make_setting_id = int(make_res["result"])

    starter_kv = [
        ("system_name", "{system_name}"),
        ("departments", "{trigger_list}"),
        ("timestamp_local", "{timestamp_local}"),
        ("transcript", "{transcript}"),
        ("audio_url", "{audio_url}"),
    ]
    for key, tpl in starter_kv:
        ok = db.execute_commit(
            """
            INSERT INTO radio_system_make_payload_fields
                (make_setting_id, field_key, field_value, field_enabled)
            VALUES (?, ?, ?, 1)
            """,
            (make_setting_id, key, tpl),
        )
        if not ok["success"]:
            raise RuntimeError(f"make field '{key}': {ok['message']}")

    # 8) transcribe
    ok = db.execute_commit(
        "INSERT INTO radio_system_transcribe_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"transcribe settings: {ok['message']}")

    # 9) address extraction settings
    ok = db.execute_commit(
        "INSERT INTO radio_system_address_extraction_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"address extraction settings: {ok['message']}")

    # 10) n8n settings
    ok = db.execute_commit(
        "INSERT INTO radio_system_n8n_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"n8n settings: {ok['message']}")

    # 11) storage settings (default LOCAL)
    ok = db.execute_commit(
        "INSERT INTO radio_system_storage_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"storage settings: {ok['message']}")

    # 12) incident classification settings (defaults: disabled, use_address_extraction_openai=1)
    ok = db.execute_commit(
        "INSERT INTO radio_system_incident_classification_settings (radio_system_id) VALUES (?)",
        (radio_system_id,),
    )
    if not ok["success"]:
        raise RuntimeError(f"incident classification settings: {ok['message']}")


    module_logger.info("Default settings inserted for radio_system_id=%s", radio_system_id)


def delete_system(db: SQLiteDatabase, radio_system_id: int | None = None, system_decimal: int | None = None) -> dict:
    """
    Delete a system by its ID **or** system_decimal.

    Returns standard `execute_commit` result dict.
    """
    if radio_system_id is None and system_decimal is None:
        return {"success": False, "message": "No radio_system_id or system_decimal provided", "result": None}

    if radio_system_id is not None:
        q, p = "DELETE FROM radio_systems WHERE radio_system_id = ?", (radio_system_id,)
    else:
        q, p = "DELETE FROM radio_systems WHERE system_decimal = ?", (system_decimal,)

    return db.execute_commit(q, p, return_row_id=False, return_count=True)


def get_systems(
        db: SQLiteDatabase,
        radio_system_id: int | List[int] | None = None,
        system_decimal: int | List[int] | None = None,
        system_name: str | List[str] | None = None,
        include_config: bool = False,
) -> dict:
    """
    Retrieve systems by optional filters. When `include_config=True`, joins the
    per-system settings and returns nested structures (upload/tone/email/etc.).

    Returns
    -------
    dict
      {
        "success": bool,
        "message": str,
        "result": [ { system rows ... } ]
      }
    """
    if not include_config:
        base = """
               SELECT rs.radio_system_id, rs.system_decimal, rs.system_name, rs.stream_url, rs.api_key
               FROM radio_systems AS rs \
               """
    else:
        base = """
               SELECT
                   rs.radio_system_id,
                   rs.system_decimal,
                   rs.system_name,
                   rs.stream_url,
                   rs.api_key,
                   rs.mute_notifications,

                   -- upload
                   rsus.split_enabled,
                   rsus.tail_min_voice_sec,
                   rsus.vad_min_speech_ratio,
                   rsus.audio_min_length,
                   rsus.voice_rms_dbfs,
                   rsus.max_split_interval,
                   rsus.max_split_length,

                   -- tone
                   rstc.tone_finder_enabled       AS tone_enabled,
                   rstc.matching_threshold        AS tone_matching_threshold,
                   rstc.fe_snr_above_noise_db            AS tone_fe_snr_above_noise_db,
                   rstc.tone_a_min_length         AS tone_a_min_length,
                   rstc.tone_b_min_length         AS tone_b_min_length,
                   rstc.two_tone_min_pair_separation_hz  AS tone_two_tone_min_pair_separation_hz,
                   rstc.hi_low_interval           AS tone_hi_low_interval,
                   rstc.hi_low_min_alternations   AS tone_hi_low_min_alternations,
                   rstc.long_tone_min_length      AS tone_long_tone_min_length,
                   rstc.pulsed_min_cycles         AS tone_pulsed_min_cycles,
                   rstc.pulsed_min_on_ms          AS tone_pulsed_min_on_ms,
                   rstc.pulsed_max_on_ms          AS tone_pulsed_max_on_ms,
                   rstc.pulsed_min_off_ms         AS tone_pulsed_min_off_ms,
                   rstc.pulsed_max_off_ms         AS tone_pulsed_max_off_ms,
                   rstc.dtmf_min_ms               AS tone_dtmf_min_ms,
                   rstc.dtmf_merge_ms             AS tone_dtmf_merge_ms,
                   rstc.dtmf_start_offset_ms      AS tone_dtmf_start_offset_ms,
                   rstc.dtmf_end_offset_ms        AS tone_dtmf_end_offset_ms,
                   rstc.dtmf_sequence_gap_s              AS tone_dtmf_sequence_gap_s,

                   -- email
                   rses.email_enabled           AS email_enabled,
                   rses.smtp_hostname           AS email_smtp_hostname,
                   rses.smtp_port               AS email_smtp_port,
                   rses.smtp_username           AS email_smtp_username,
                   rses.smtp_password           AS email_smtp_password,
                   rses.email_address_from      AS email_address_from,
                   rses.email_text_from         AS email_text_from,
                   rses.email_alert_subject     AS email_alert_subject,
                   rses.email_alert_body        AS email_alert_body,

                   -- pushover
                   rsps.pushover_enabled        AS pushover_enabled,
                   rsps.pushover_group_token    AS pushover_group_token,
                   rsps.pushover_app_token      AS pushover_app_token,
                   rsps.pushover_body           AS pushover_body,
                   rsps.pushover_subject        AS pushover_subject,
                   rsps.pushover_sound          AS pushover_sound,

                   -- telegram
                   rsts.telegram_enabled        AS telegram_enabled,
                   rsts.telegram_bot_token      AS telegram_bot_token,
                   rsts.telegram_channel_id     AS telegram_channel_id,
                   rsts.telegram_message_body   AS telegram_message_body,

                   -- discord
                   rsds.discord_setting_id      AS discord_setting_id,
                   rsds.discord_enabled         AS discord_enabled,
                   rsds.discord_render_map      AS discord_render_map,
                   rsds.discord_attach_audio    AS discord_attach_audio,
                   rsds.discord_webhook_url     AS discord_webhook_url,
                   rsds.discord_embed_title     AS discord_embed_title,
                   rsds.discord_embed_color     AS discord_embed_color,
                   rsds.discord_embed_footer    AS discord_embed_footer,

                   -- make
                   rsms.make_setting_id         AS make_setting_id,
                   rsms.make_enabled            AS make_enabled,
                   rsms.make_webhook_url        AS make_webhook_url,
                   rsms.make_api_key            AS make_api_key,

                   -- transcribe
                   rstr.transcribe_setting_id   AS transcribe_setting_id,
                   rstr.transcribe_enabled      AS transcribe_enabled,
                   rstr.transcribe_url          AS transcribe_url,
                   rstr.transcribe_api_key      AS transcribe_api_key,
                   rstr.transcribe_model        AS transcribe_model,
                   rstr.transcribe_language     AS transcribe_language,
                   rstr.transcribe_prompt       AS transcribe_prompt,

                   -- address extraction
                   rsae.address_extraction_setting_id   AS address_extraction_setting_id,
                   rsae.address_extraction_enabled      AS address_extraction_enabled,
                   rsae.openai_api_key                  AS address_extraction_openai_api_key,
                   rsae.openai_model                    AS address_extraction_openai_model,
                   rsae.google_maps_api_key             AS address_extraction_google_maps_api_key,
                   rsae.geocode_country                 AS address_extraction_geocode_country,
                   rsae.geocode_state                   AS address_extraction_geocode_state,
                   rsae.geocode_city                    AS address_extraction_geocode_city,

                   -- n8n
                   rsn8n.n8n_setting_id         AS n8n_setting_id,
                   rsn8n.n8n_enabled            AS n8n_enabled,
                   rsn8n.n8n_webhook_url        AS n8n_webhook_url,
                   rsn8n.n8n_timeout_s          AS n8n_timeout_s,
                   rsn8n.jwt_passphrase         AS n8n_jwt_passphrase,
                   rsn8n.jwt_issuer             AS n8n_jwt_issuer,
                   rsn8n.jwt_audience           AS n8n_jwt_audience,
                   rsn8n.jwt_subject_template   AS n8n_jwt_subject_template,
                   rsn8n.jwt_ttl_s              AS n8n_jwt_ttl_s,

                   -- storage
                   rsst.storage_type            AS storage_type,
                   rsst.path_pattern            AS storage_path_pattern,

                   rsst.sftp_host               AS storage_sftp_host,
                   rsst.sftp_port               AS storage_sftp_port,
                   rsst.sftp_username           AS storage_sftp_username,
                   rsst.sftp_password           AS storage_sftp_password,
                   rsst.sftp_use_ssh_key            AS storage_sftp_use_ssh_key,
                   rsst.sftp_remote_path        AS storage_sftp_remote_path,
                   rsst.sftp_base_url           AS storage_sftp_base_url,
                   rsst.sftp_timeout_s          AS storage_sftp_timeout_s,
                   rsst.sftp_max_retries        AS storage_sftp_max_retries,

                   rsst.s3_bucket               AS storage_s3_bucket,
                   rsst.s3_region               AS storage_s3_region,
                   rsst.s3_access_key_id        AS storage_s3_access_key_id,
                   rsst.s3_secret_access_key    AS storage_s3_secret_access_key,
                   rsst.s3_endpoint_url         AS storage_s3_endpoint_url,
                   rsst.s3_object_key_prefix    AS storage_s3_object_key_prefix,

                   -- incident classification
                   rsics.incident_classification_setting_id AS incident_classification_setting_id,
                   rsics.incident_classification_enabled    AS incident_classification_enabled,
                   rsics.openai_api_key                     AS incident_openai_api_key,
                   rsics.openai_model                       AS incident_openai_model,
                   rsics.min_confidence                     AS incident_min_confidence
                   

               FROM radio_systems AS rs
                        LEFT JOIN radio_system_upload_settings   rsus ON rs.radio_system_id = rsus.radio_system_id
                        LEFT JOIN radio_system_tone_settings     rstc ON rs.radio_system_id = rstc.radio_system_id
                        LEFT JOIN radio_system_email_settings    rses ON rs.radio_system_id = rses.radio_system_id
                        LEFT JOIN radio_system_pushover_settings rsps ON rs.radio_system_id = rsps.radio_system_id
                        LEFT JOIN radio_system_telegram_settings rsts ON rs.radio_system_id = rsts.radio_system_id
                        LEFT JOIN radio_system_discord_settings  rsds ON rs.radio_system_id = rsds.radio_system_id
                        LEFT JOIN radio_system_make_settings     rsms ON rs.radio_system_id = rsms.radio_system_id
                        LEFT JOIN radio_system_transcribe_settings rstr ON rs.radio_system_id = rstr.radio_system_id
                        LEFT JOIN radio_system_address_extraction_settings rsae ON rs.radio_system_id = rsae.radio_system_id
                        LEFT JOIN radio_system_n8n_settings      rsn8n ON rs.radio_system_id = rsn8n.radio_system_id
                        LEFT JOIN radio_system_storage_settings rsst ON rs.radio_system_id = rsst.radio_system_id
                        LEFT JOIN radio_system_incident_classification_settings rsics ON rs.radio_system_id = rsics.radio_system_id \
               """

    conditions: List[str] = []
    params: List[Any] = []

    # radio_system_id filter
    if radio_system_id is not None:
        if isinstance(radio_system_id, list):
            clause, vals = _in_clause("rs.radio_system_id", radio_system_id)
            conditions.append(clause)
            params.extend(vals)
        else:
            conditions.append("rs.radio_system_id = ?")
            params.append(radio_system_id)

    # system_decimal filter
    if system_decimal is not None:
        if isinstance(system_decimal, list):
            clause, vals = _in_clause("rs.system_decimal", system_decimal)
            conditions.append(clause)
            params.extend(vals)
        else:
            conditions.append("rs.system_decimal = ?")
            params.append(system_decimal)

    # system_name filter
    if system_name is not None:
        if isinstance(system_name, list):
            clause, vals = _in_clause("rs.system_name", system_name)
            conditions.append(clause)
            params.extend(vals)
        else:
            conditions.append("rs.system_name = ?")
            params.append(system_name)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    # GROUP BY to dedupe left-joins; ORDER BY name
    tail = " GROUP BY rs.radio_system_id ORDER BY rs.system_name"

    query = base + where + tail

    res = db.execute_query(query, tuple(params), fetch_mode="all")
    if not res.get("success"):
        module_logger.error("Failed to retrieve systems: %s", res.get("message"))
        return {"success": False, "message": f"Failed to retrieve systems: {res.get('message')}", "result": []}

    rows = res["result"]
    systems: List[Dict[str, Any]] = []

    for row in rows:
        sys_obj: Dict[str, Any] = {
            "radio_system_id": row["radio_system_id"],
            "system_decimal": row["system_decimal"],
            "system_name": row["system_name"],
            "stream_url": row["stream_url"],
            "api_key": row["api_key"],
        }

        if include_config:
            # Upload
            sys_obj["upload"] = {
                "split_enabled": row.get("split_enabled"),
                "tail_min_voice_sec": row.get("tail_min_voice_sec"),
                "vad_min_speech_ratio": row.get("vad_min_speech_ratio"),
                "voice_rms_dbfs": row.get("voice_rms_dbfs"),
                "max_split_interval": row.get("max_split_interval"),
                "max_split_length": row.get("max_split_length"),
                "audio_min_length": row.get("audio_min_length"),
            }

            # Tone
            sys_obj["tone"] = {
                "enabled": row.get("tone_enabled"),
                "matching_threshold": row.get("tone_matching_threshold"),
                "fe_snr_above_noise_db": row.get("tone_fe_snr_above_noise_db"),
                "tone_a_min_length": row.get("tone_a_min_length"),
                "tone_b_min_length": row.get("tone_b_min_length"),
                "two_tone_min_pair_separation_hz": row.get("tone_two_tone_min_pair_separation_hz"),
                "hi_low_interval": row.get("tone_hi_low_interval"),
                "hi_low_min_alternations": row.get("tone_hi_low_min_alternations"),
                "long_tone_min_length": row.get("tone_long_tone_min_length"),
                "pulsed_min_cycles": row.get("tone_pulsed_min_cycles"),
                "pulsed_min_on_ms": row.get("tone_pulsed_min_on_ms"),
                "pulsed_max_on_ms": row.get("tone_pulsed_max_on_ms"),
                "pulsed_min_off_ms": row.get("tone_pulsed_min_off_ms"),
                "pulsed_max_off_ms": row.get("tone_pulsed_max_off_ms"),
                "dtmf_min_ms": row.get("tone_dtmf_min_ms"),
                "dtmf_merge_ms": row.get("tone_dtmf_merge_ms"),
                "dtmf_start_offset_ms": row.get("tone_dtmf_start_offset_ms"),
                "dtmf_end_offset_ms": row.get("tone_dtmf_end_offset_ms"),
                "dtmf_sequence_gap_s": row.get("tone_dtmf_sequence_gap_s"),
            }

            # Email
            sys_obj["email"] = {
                "enabled": row.get("email_enabled"),
                "smtp_hostname": row.get("email_smtp_hostname"),
                "smtp_port": row.get("email_smtp_port"),
                "smtp_username": row.get("email_smtp_username"),
                "smtp_password": row.get("email_smtp_password"),
                "email_address_from": row.get("email_address_from"),
                "email_text_from": row.get("email_text_from"),
                "email_alert_subject": row.get("email_alert_subject"),
                "email_alert_body": row.get("email_alert_body"),
                "recipients": [],
            }

            # Pushover
            sys_obj["pushover"] = {
                "enabled": row.get("pushover_enabled"),
                "group_token": row.get("pushover_group_token"),
                "app_token": row.get("pushover_app_token"),
                "body": row.get("pushover_body"),
                "subject": row.get("pushover_subject"),
                "sound": row.get("pushover_sound"),
            }

            # Telegram
            sys_obj["telegram"] = {
                "enabled": row.get("telegram_enabled"),
                "bot_token": row.get("telegram_bot_token"),
                "channel_id": row.get("telegram_channel_id"),
                "message_body": row.get("telegram_message_body"),
            }

            # Discord
            sys_obj["discord"] = {
                "discord_setting_id": row.get("discord_setting_id"),
                "enabled": row.get("discord_enabled"),
                "webhook_url": row.get("discord_webhook_url"),
                "embed_title": row.get("discord_embed_title"),
                "embed_color": row.get("discord_embed_color"),
                "embed_footer": row.get("discord_embed_footer"),
                "render_map": row.get("discord_render_map"),
                "attach_audio": row.get("discord_attach_audio"),
                "fields": [],
            }

            discord_setting_id = row.get("discord_setting_id")
            if discord_setting_id is not None:
                df = get_system_discord_fields(db, discord_setting_id=discord_setting_id)
                if df.get("success"):
                    sys_obj["discord"]["fields"] = [
                        {
                            "embed_field_id": f["embed_field_id"],
                            "field_key": f["field_key"],
                            "field_label": f["field_label"],
                            "field_template": f["field_template"],
                            "field_inline": bool(f["field_inline"]),
                            "field_enabled": bool(f["field_enabled"]),
                            "sort_order": f["sort_order"],
                        }
                        for f in df["result"]
                    ]
                else:
                    module_logger.error(
                        "Error loading discord fields for discord_setting_id=%s: %s",
                        discord_setting_id,
                        df.get("message"),
                    )

            # Make
            sys_obj["make"] = {
                "make_setting_id": row.get("make_setting_id"),
                "enabled": row.get("make_enabled"),
                "webhook_url": row.get("make_webhook_url"),
                "api_key": row.get("make_api_key"),
                "fields": [],
            }
            make_setting_id = row.get("make_setting_id")
            if make_setting_id:
                mf = get_system_make_fields(db, make_setting_id=make_setting_id)
                if mf.get("success"):
                    sys_obj["make"]["fields"] = [
                        {
                            "payload_field_id": f["payload_field_id"],
                            "field_key": f["field_key"],
                            "field_value": f["field_value"],
                            "field_enabled": bool(f["field_enabled"]),
                        }
                        for f in mf["result"]
                    ]
                else:
                    module_logger.error(
                        "Error loading make payload fields for make_setting_id=%s: %s",
                        make_setting_id,
                        mf.get("message"),
                    )

            # Email recipients (1:N)
            emails_res = get_system_emails(db, radio_system_id=row["radio_system_id"])
            if emails_res.get("success"):
                sys_obj["email"]["recipients"] = emails_res["result"]

            # Transcribe
            sys_obj["transcribe"] = {
                "transcribe_setting_id": row.get("transcribe_setting_id"),
                "enabled": row.get("transcribe_enabled"),
                "url": row.get("transcribe_url"),
                "api_key": row.get("transcribe_api_key"),
                "model": row.get("transcribe_model"),
                "language": row.get("transcribe_language"),
                "prompt": row.get("transcribe_prompt"),
            }

            # Address Extraction
            sys_obj["address_extraction"] = {
                "address_extraction_setting_id": row.get("address_extraction_setting_id"),
                "enabled": row.get("address_extraction_enabled"),
                "openai_api_key": row.get("address_extraction_openai_api_key"),
                "openai_model": row.get("address_extraction_openai_model"),
                "google_maps_api_key": row.get("address_extraction_google_maps_api_key"),
                "geocode_country": row.get("address_extraction_geocode_country"),
                "geocode_state": row.get("address_extraction_geocode_state"),
                "geocode_city": row.get("address_extraction_geocode_city"),
                "regions": [],
            }

            address_extraction_setting_id = row.get("address_extraction_setting_id")
            if address_extraction_setting_id is not None:
                regions_res = get_geocoding_regions(db, address_extraction_setting_id=address_extraction_setting_id)
                if regions_res.get("success"):
                    sys_obj["address_extraction"]["regions"] = [
                        {
                            "region_id": r["region_id"],
                            "state_code": r["state_code"],
                            "county_name": r["county_name"],
                            "priority": r["priority"],
                        }
                        for r in regions_res["result"]
                    ]
                else:
                    module_logger.error(
                        "Error loading geocoding regions for address_extraction_setting_id=%s: %s",
                        address_extraction_setting_id,
                        regions_res.get("message"),
                    )

            # N8N
            sys_obj["n8n"] = {
                "n8n_setting_id": row.get("n8n_setting_id"),
                "enabled": int(row.get("n8n_enabled") or 0),
                "webhook_url": row.get("n8n_webhook_url"),
                "timeout_s": int(row.get("n8n_timeout_s") or 10),
                "jwt_passphrase": row.get("n8n_jwt_passphrase"),
                "jwt_issuer": row.get("n8n_jwt_issuer"),
                "jwt_audience": row.get("n8n_jwt_audience"),
                "jwt_subject_template": row.get("n8n_jwt_subject_template"),
                "jwt_ttl_s": int(row.get("n8n_jwt_ttl_s") or 300),
            }

            # Storage
            key_path = SSH_KEY_ROOT / str(row["radio_system_id"]) / "id_rsa"
            ssh_key_exists = key_path.is_file()

            sys_obj["storage"] = {
                "storage_type": (row.get("storage_type") or "LOCAL"),
                "path_pattern": row.get("storage_path_pattern") or "%Y/%m/%d",

                # SFTP / SCP
                "host": row.get("storage_sftp_host"),
                "port": row.get("storage_sftp_port"),
                "username": row.get("storage_sftp_username"),
                "password": row.get("storage_sftp_password"),
                "use_ssh_key": row.get("storage_sftp_use_ssh_key"),
                "ssh_key_exists": ssh_key_exists,
                "remote_path": row.get("storage_sftp_remote_path"),
                "base_url": row.get("storage_sftp_base_url"),
                "timeout_s": row.get("storage_sftp_timeout_s"),
                "max_retries": row.get("storage_sftp_max_retries"),


                # S3 / compatible
                "bucket": row.get("storage_s3_bucket"),
                "region": row.get("storage_s3_region"),
                "access_key_id": row.get("storage_s3_access_key_id"),
                "secret_access_key": row.get("storage_s3_secret_access_key"),
                "endpoint_url": row.get("storage_s3_endpoint_url"),
                "object_key_prefix": row.get("storage_s3_object_key_prefix"),
            }

            # Incident Classification
            sys_obj["incident_classification"] = {
                "incident_classification_setting_id": row.get("incident_classification_setting_id"),
                "enabled": row.get("incident_classification_enabled") or 0,
                "openai_api_key": row.get("incident_openai_api_key"),
                "openai_model": row.get("incident_openai_model") or "gpt-4o-mini",
                "min_confidence": row.get("incident_min_confidence") if row.get("incident_min_confidence") is not None else 0.0,
            }

        systems.append(sys_obj)

    return {"success": True, "message": "Systems retrieved successfully.", "result": systems}


# ===========================================================================
# System – General updates
# ===========================================================================
def update_system_general(db: SQLiteDatabase, system_data: dict) -> dict:
    """
    Update an existing system's basic fields.

    Expected keys:
      - radio_system_id (required)
      - system_decimal  (optional; updates if changed)
      - system_name     (optional; updates if truthy)
      - stream_url      (optional; updates if provided, even blank)
      - api_key         (optional; updates if provided and changed)
      - post_tone_delay (optional; seconds to skip after tone)
    """
    try:
        radio_system_id = int(system_data.get("radio_system_id") or 0)
        if not radio_system_id:
            return {"success": False, "message": "radio_system_id field is required", "result": []}

        system_decimal = system_data.get("system_decimal")
        system_decimal = int(system_decimal) if system_decimal is not None and system_decimal != "" else None

        system_name = system_data.get("system_name")
        system_name = system_name.strip() if isinstance(system_name, str) else system_name

        stream_url = system_data.get("stream_url")
        # stream_url: keep None if missing; allow "" to clear

        post_tone_delay = system_data.get("post_tone_delay")
        if post_tone_delay is not None:
            post_tone_delay = int(post_tone_delay) if post_tone_delay != "" else 0

        # api_key: only consider update if key was actually included in payload
        api_key_present = "api_key" in system_data
        api_key: Optional[str] = system_data.get("api_key") if api_key_present else None
        if api_key is not None:
            api_key = str(api_key).strip()

    except ValueError:
        return {"success": False, "message": "One or more fields are not of the right data type.", "result": []}

    # Load current values (so we can do “update only if changed” like your original)
    cur = db.execute_query(
        "SELECT system_decimal, system_name, stream_url, api_key "
        "FROM radio_systems WHERE radio_system_id = ?",
        (radio_system_id,),
        fetch_mode="one",
    )
    if not cur.get("success") or not cur.get("result"):
        return {"success": False, "message": "System not found or query failed", "result": []}

    cur_row = cur["result"]
    old_decimal = cur_row.get("system_decimal")
    old_name    = cur_row.get("system_name") or ""
    old_stream  = cur_row.get("stream_url")
    old_key     = cur_row.get("api_key") or ""
    old_post_tone_delay = int(cur_row.get("post_tone_delay") or 0)

    sets, params = [], []

    # system_decimal: only if provided and different
    if system_decimal is not None and system_decimal != old_decimal:
        sets.append("system_decimal = ?")
        params.append(system_decimal)

    # system_name: preserve your “truthy only” behavior
    if system_name:
        if system_name != old_name:
            sets.append("system_name = ?")
            params.append(system_name)

    # stream_url: preserve your “is not None” behavior (allows clearing to "")
    if stream_url is not None:
        if stream_url != old_stream:
            sets.append("stream_url = ?")
            params.append(stream_url)

    # api_key: only if included in payload AND changed
    if api_key_present:
        if api_key is None or api_key == "":
            return {"success": False, "message": "API key cannot be blank.", "result": []}

        if api_key != old_key:
            if not _APIKEY_RE.match(api_key):
                return {
                    "success": False,
                    "message": "API key must be 16–128 chars and URL-safe (A–Z, a–z, 0–9, _- . ~).",
                    "result": []
                }
            sets.append("api_key = ?")
            params.append(api_key)

    # post_tone_delay: update if provided
    if post_tone_delay is not None and post_tone_delay != old_post_tone_delay:
        sets.append("post_tone_delay = ?")
        params.append(post_tone_delay)

    if not sets:
        return {"success": False, "message": "No fields to update", "result": []}

    params.append(radio_system_id)
    q = f"UPDATE radio_systems SET {', '.join(sets)} WHERE radio_system_id = ?"
    upd = db.execute_commit(q, tuple(params), return_count=True)

    if not upd.get("success"):
        # Keep your original message style, but include DB’s message if present
        return {"success": False, "message": upd.get("message") or "Failed to update system", "result": []}

    return {"success": True, "message": "System updated successfully", "result": []}

# ===========================================================================
# Tone settings
# ===========================================================================
def update_system_tone_settings(db: SQLiteDatabase, data: dict) -> dict:
    """
    PATCH-style update for radio_system_tone_settings.
    Creates the row if missing (ON CONFLICT upsert).

    Expected keys:
      radio_system_id, tone_finder_enabled, matching_threshold, fe_snr_above_noise_db,
      tone_a_min_length, tone_b_min_length, two_tone_min_pair_separation_hz,
      hi_low_interval, hi_low_min_alternations, long_tone_min_length,
      pulsed_min_cycles, pulsed_min_on_ms, pulsed_max_on_ms,
      pulsed_min_off_ms, pulsed_max_off_ms,
      dtmf_min_ms, dtmf_merge_ms, dtmf_start_offset_ms, dtmf_end_offset_ms, dtmf_sequence_gap_s
    """
    def _float_default(v, default: float) -> float:
        if v is None or v == "":
            return float(default)
        return float(v)

    def _int_default(v, default: int) -> int:
        if v is None or v == "":
            return int(default)
        return int(v)

    sys_id = int(data["radio_system_id"])

    q = """
        INSERT INTO radio_system_tone_settings
        (
            radio_system_id,
            tone_finder_enabled, matching_threshold,
            tone_a_min_length, tone_b_min_length,
            hi_low_interval, hi_low_min_alternations,
            long_tone_min_length,
            pulsed_min_cycles, pulsed_min_on_ms, pulsed_max_on_ms,
            pulsed_min_off_ms, pulsed_max_off_ms,
            dtmf_min_ms, dtmf_merge_ms, dtmf_start_offset_ms, dtmf_end_offset_ms,
            fe_snr_above_noise_db,
            two_tone_min_pair_separation_hz,
            dtmf_sequence_gap_s
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (radio_system_id) DO UPDATE SET
                                                    tone_finder_enabled      = excluded.tone_finder_enabled,
                                                    matching_threshold       = excluded.matching_threshold,
                                                    tone_a_min_length        = excluded.tone_a_min_length,
                                                    tone_b_min_length        = excluded.tone_b_min_length,
                                                    hi_low_interval          = excluded.hi_low_interval,
                                                    hi_low_min_alternations  = excluded.hi_low_min_alternations,
                                                    long_tone_min_length     = excluded.long_tone_min_length,
                                                    pulsed_min_cycles        = excluded.pulsed_min_cycles,
                                                    pulsed_min_on_ms         = excluded.pulsed_min_on_ms,
                                                    pulsed_max_on_ms         = excluded.pulsed_max_on_ms,
                                                    pulsed_min_off_ms        = excluded.pulsed_min_off_ms,
                                                    pulsed_max_off_ms        = excluded.pulsed_max_off_ms,
                                                    dtmf_min_ms              = excluded.dtmf_min_ms,
                                                    dtmf_merge_ms            = excluded.dtmf_merge_ms,
                                                    dtmf_start_offset_ms     = excluded.dtmf_start_offset_ms,
                                                    dtmf_end_offset_ms       = excluded.dtmf_end_offset_ms,

                                                    fe_snr_above_noise_db           = excluded.fe_snr_above_noise_db,
                                                    two_tone_min_pair_separation_hz = excluded.two_tone_min_pair_separation_hz,
                                                    dtmf_sequence_gap_s             = excluded.dtmf_sequence_gap_s \
        """

    params = (
        sys_id,
        _bool_like(data.get("tone_finder_enabled")),
        _float_default(data.get("matching_threshold"), 0.0),
        _float_default(data.get("tone_a_min_length"), 0.0),
        _float_default(data.get("tone_b_min_length"), 0.0),
        _float_default(data.get("hi_low_interval"), 0.0),
        _int_default(data.get("hi_low_min_alternations"), 0),
        _float_default(data.get("long_tone_min_length"), 0.0),
        _int_default(data.get("pulsed_min_cycles"), 6),
        _int_default(data.get("pulsed_min_on_ms"), 120),
        _int_default(data.get("pulsed_max_on_ms"), 900),
        _int_default(data.get("pulsed_min_off_ms"), 25),
        _int_default(data.get("pulsed_max_off_ms"), 350),
        _int_default(data.get("dtmf_min_ms"), 100),
        _int_default(data.get("dtmf_merge_ms"), 75),
        _int_default(data.get("dtmf_start_offset_ms"), -20),
        _int_default(data.get("dtmf_end_offset_ms"), 20),
        _float_default(data.get("fe_snr_above_noise_db"), 1.0),
        _int_default(data.get("two_tone_min_pair_separation_hz"), 10),
        _float_default(data.get("dtmf_sequence_gap_s"), 0.3),
    )

    return db.execute_commit(q, params, return_row_id=False, return_count=True)


# ===========================================================================
# Telegram
# ===========================================================================
def update_system_telegram_settings(db: SQLiteDatabase, system_data: dict) -> dict:
    """
    Update Telegram settings for a system.
    """
    q = """
        UPDATE radio_system_telegram_settings
        SET telegram_enabled    = ?,
            telegram_bot_token  = ?,
            telegram_channel_id = ?,
            telegram_message_body = ?
        WHERE radio_system_id = ? \
        """
    params = (
        _bool_like(system_data.get("telegram_enabled")),
        system_data.get("telegram_bot_token") or None,
        system_data.get("telegram_channel_id") or None,
        system_data.get("telegram_message_body"),
        system_data.get("radio_system_id"),
    )
    try:
        return db.execute_commit(q, params, return_count=True)
    except Exception as e:
        module_logger.error("Unexpected Exception when saving system telegram settings: %s", e)
        return {"success": False, "message": f"Unexpected Exception when saving system telegram settings: {e}", "result": []}


# ===========================================================================
# Discord
# ===========================================================================
def update_system_discord_settings(db: SQLiteDatabase, system_data: dict) -> dict:
    """
    Update Discord settings for a system.

    Required:
      - radio_system_id

    Optional:
      - discord_enabled (0/1/bool)
      - discord_render_map (0/1/bool)
      - discord_attach_audio (0/1/bool)
      - discord_webhook_url
      - discord_embed_title
      - discord_embed_color
      - discord_embed_footer
    """
    rsid = system_data.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id required.", "result": []}

    enabled       = _bool_like(system_data.get("discord_enabled"))
    render_map    = _bool_like(system_data.get("discord_render_map"))
    attach_audio  = _bool_like(system_data.get("discord_attach_audio"))

    q_update = """
               UPDATE radio_system_discord_settings
               SET discord_enabled       = ?,
                   discord_render_map    = ?,
                   discord_attach_audio  = ?,
                   discord_webhook_url   = ?,
                   discord_embed_title   = ?,
                   discord_embed_color   = ?,
                   discord_embed_footer  = ?
               WHERE radio_system_id = ? \
               """
    params_update = (
        enabled,
        render_map,
        attach_audio,
        system_data.get("discord_webhook_url") or None,
        system_data.get("discord_embed_title") or None,
        system_data.get("discord_embed_color") or None,
        system_data.get("discord_embed_footer") or None,
        rsid,
    )

    res = db.execute_commit(q_update, params_update, return_row_id=False, return_count=True)
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}

    # If no row existed, insert it.
    # NOTE: execute_commit(..., return_count=True) typically returns an affected-row count;
    # adapt the extraction below to your SQLiteDatabase wrapper if needed.
    affected = 0
    try:
        affected = int(res.get("result") or 0)
    except Exception:
        # If your wrapper returns something else, leave affected=0 and we’ll still be safe.
        pass

    if affected == 0:
        q_insert = """
                   INSERT INTO radio_system_discord_settings (
                       radio_system_id,
                       discord_enabled,
                       discord_render_map,
                       discord_attach_audio,
                       discord_webhook_url,
                       discord_embed_title,
                       discord_embed_color,
                       discord_embed_footer
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) \
                   """
        params_insert = (
            rsid,
            enabled,
            render_map,
            attach_audio,
            system_data.get("discord_webhook_url") or None,
            system_data.get("discord_embed_title") or None,
            system_data.get("discord_embed_color") or None,
            system_data.get("discord_embed_footer") or None,
        )
        ins = db.execute_commit(q_insert, params_insert, return_count=True)
        if not ins.get("success"):
            return {"success": False, "message": ins.get("message"), "result": []}

    return {"success": True, "message": "Discord settings updated.", "result": []}


def get_system_discord_fields(
        db: SQLiteDatabase,
        *,
        discord_setting_id: int,
        enabled: int | bool | None = None,
):
    """
    Fetch Discord embed fields for a given settings row.

    Filters by `enabled` if provided (accepts 1/0 or bool).
    """
    q = """
        SELECT embed_field_id,
               discord_setting_id,
               field_key,
               field_label,
               field_template,
               field_inline,
               field_enabled,
               sort_order
        FROM radio_system_discord_embed_fields
        WHERE discord_setting_id = ? \
        """
    params: List[Any] = [discord_setting_id]
    if enabled is not None:
        if isinstance(enabled, bool):
            enabled = 1 if enabled else 0
        q += " AND field_enabled = ?"
        params.append(enabled)
    q += " ORDER BY sort_order ASC"
    return db.execute_query(q, tuple(params), fetch_mode="all")


def _get_discord_setting_id(db: SQLiteDatabase, radio_system_id: int) -> dict:
    """
    Resolve discord_setting_id for a given radio_system_id.
    Returns {success, message, result: int|None}.
    """
    q = "SELECT discord_setting_id FROM radio_system_discord_settings WHERE radio_system_id = ?"
    res = db.execute_query(q, (radio_system_id,), fetch_mode="one")
    if not res.get("success"):
        return {"success": False, "message": f"DB error: {res.get('message')}", "result": None}
    row = res.get("result")
    if not row:
        return {"success": False, "message": f"No discord_setting row for radio_system_id={radio_system_id}", "result": None}
    return {"success": True, "message": "ok", "result": row["discord_setting_id"]}


def add_or_update_system_discord_field(db: SQLiteDatabase, field_data: dict):
    """
    Insert or update a row in radio_system_discord_embed_fields.

    UPDATE path: pass 'embed_field_id'.
    INSERT path: pass 'discord_setting_id' OR 'radio_system_id' (we will resolve),
                 plus: field_key, field_label, field_template.

    Optional:
      field_inline  (0/1/bool; default 0)
      field_enabled (0/1/bool; default 1)
      sort_order    (int; if None auto MAX+10)
    """
    embed_field_id = field_data.get("embed_field_id")
    discord_setting_id = field_data.get("discord_setting_id")
    radio_system_id = field_data.get("radio_system_id")
    field_key = field_data.get("field_key")
    field_label = field_data.get("field_label")
    field_template = field_data.get("field_template")
    field_inline = _bool_like(field_data.get("field_inline", 0))
    field_enabled = _bool_like(field_data.get("field_enabled", 1))
    sort_order = field_data.get("sort_order")

    # UPDATE
    if embed_field_id is not None:
        if sort_order is None:
            curr = db.execute_query(
                "SELECT sort_order FROM radio_system_discord_embed_fields WHERE embed_field_id = ?",
                (embed_field_id,),
                fetch_mode="one",
            )
            sort_order = curr["result"]["sort_order"] if curr.get("success") and curr.get("result") else 0

        q = """
            UPDATE radio_system_discord_embed_fields
            SET field_key      = ?,
                field_label    = ?,
                field_template = ?,
                field_inline   = ?,
                field_enabled  = ?,
                sort_order     = ?
            WHERE embed_field_id = ? \
            """
        return db.execute_commit(
            q, (field_key, field_label, field_template, field_inline, field_enabled, sort_order, embed_field_id),
            return_count=True
        )

    # INSERT
    if discord_setting_id is None:
        if radio_system_id is None:
            return {"success": False, "message": "discord_setting_id or radio_system_id required for insert.", "result": []}
        rs_res = _get_discord_setting_id(db, int(radio_system_id))
        if not rs_res.get("success"):
            return {"success": False, "message": rs_res.get("message"), "result": []}
        discord_setting_id = rs_res["result"]

    missing = []
    if not field_key:      missing.append("field_key")
    if not field_label:    missing.append("field_label")
    if not field_template: missing.append("field_template")
    if missing:
        return {"success": False, "message": "Missing required fields: " + ", ".join(missing), "result": []}

    # auto sort if needed
    if sort_order is None:
        mx = db.execute_query(
            "SELECT COALESCE(MAX(sort_order), -10) AS max_sort FROM radio_system_discord_embed_fields WHERE discord_setting_id = ?",
            (discord_setting_id,),
            fetch_mode="one",
        )
        sort_order = (mx["result"]["max_sort"] + 10) if mx.get("success") and mx.get("result") else 0

    q = """
        INSERT INTO radio_system_discord_embed_fields
        (discord_setting_id, field_key, field_label, field_template, field_inline, field_enabled, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?) \
        """
    return db.execute_commit(
        q, (discord_setting_id, field_key, field_label, field_template, field_inline, field_enabled, sort_order),
        return_row_id=True
    )


def delete_system_discord_field(
        db: SQLiteDatabase,
        *,
        embed_field_id: int | None = None,
        discord_setting_id: int | None = None,
        field_key: str | None = None,
        radio_system_id: int | None = None,
):
    """
    Delete a Discord embed field.

    Preferred: embed_field_id.
    Alternate: (discord_setting_id + field_key) OR (radio_system_id + field_key).
    """
    if embed_field_id is not None:
        sel_q = "SELECT embed_field_id FROM radio_system_discord_embed_fields WHERE embed_field_id = ?"
        sel_params = (embed_field_id,)
    else:
        if not field_key:
            return {"success": False, "message": "embed_field_id OR (field_key + discord_setting_id|radio_system_id) required.", "result": []}
        if discord_setting_id is None:
            if radio_system_id is None:
                return {"success": False, "message": "field_key requires discord_setting_id or radio_system_id.", "result": []}
            rs_res = _get_discord_setting_id(db, int(radio_system_id))
            if not rs_res.get("success"):
                return {"success": False, "message": rs_res.get("message"), "result": []}
            discord_setting_id = rs_res["result"]

        sel_q = """
                SELECT embed_field_id
                FROM radio_system_discord_embed_fields
                WHERE discord_setting_id = ?
                  AND field_key = ? \
                """
        sel_params = (discord_setting_id, field_key)

    sel_res = db.execute_query(sel_q, sel_params, fetch_mode="one")
    if not sel_res.get("success"):
        return {"success": False, "message": sel_res.get("message"), "result": []}
    row = sel_res.get("result")
    if not row:
        return {"success": False, "message": "Embed field not found.", "result": []}

    efid = row["embed_field_id"]
    del_res = db.execute_commit(
        "DELETE FROM radio_system_discord_embed_fields WHERE embed_field_id = ?",
        (efid,),
        return_count=True,
        return_row_id=False,
    )
    if not del_res.get("success"):
        return {"success": False, "message": del_res.get("message"), "result": []}
    if del_res.get("result", 0) == 0:
        return {"success": False, "message": "No embed field deleted.", "result": []}

    return {"success": True, "message": "Discord embed field deleted.", "result": []}


def reorder_system_discord_fields(db: SQLiteDatabase, discord_setting_id: int, ordered_ids: list[int]):
    """
    Update sort_order for the given embed_field_id list (top→bottom as 10,20,30…).

    Returns db-style result dict.
    """
    if not ordered_ids:
        return {"success": False, "message": "No field IDs provided.", "result": []}

    # Build CASE expression
    case_parts: List[str] = []
    params: List[Any] = []
    for idx, efid in enumerate(ordered_ids):
        sort_val = (idx + 1) * 10
        case_parts.append("WHEN ? THEN ?")
        params.extend([efid, sort_val])

    placeholders = ",".join("?" for _ in ordered_ids)
    case_sql = " ".join(case_parts)
    q = f"""
        UPDATE radio_system_discord_embed_fields
           SET sort_order = CASE embed_field_id {case_sql} ELSE sort_order END
         WHERE discord_setting_id = ?
           AND embed_field_id IN ({placeholders})
    """
    params.append(discord_setting_id)
    params.extend(ordered_ids)

    return db.execute_commit(q, tuple(params), return_count=True)


# ===========================================================================
# Make settings
# ===========================================================================
def update_system_make_settings(db: SQLiteDatabase, payload: dict):
    """
    PATCH-style updater for radio_system_make_settings.

    Keys (all optional except radio_system_id):
      - radio_system_id   (int, required)
      - make_enabled      (0/1/bool)
      - make_webhook_url  (str|None)
      - make_api_key      (str|None)
    """
    rsid = payload.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id required", "result": []}

    fields, params = [], []
    if "make_enabled" in payload:
        fields.append("make_enabled = ?")
        params.append(_bool_like(payload["make_enabled"]))
    if "make_webhook_url" in payload:
        fields.append("make_webhook_url = ?")
        params.append(payload.get("make_webhook_url"))
    if "make_api_key" in payload:
        fields.append("make_api_key = ?")
        params.append(payload.get("make_api_key"))

    if not fields:
        return {"success": True, "message": "No changes", "result": []}

    params.append(rsid)
    q = f"UPDATE radio_system_make_settings SET {', '.join(fields)} WHERE radio_system_id = ?"
    return db.execute_commit(q, tuple(params), return_count=True)


def get_system_make_fields(db: SQLiteDatabase, make_setting_id: int, enabled: int | bool | None = None):
    """
    Fetch Make payload fields, optionally filtering by enabled flag.
    """
    sql = """
          SELECT payload_field_id, make_setting_id, field_key, field_value, field_enabled
          FROM radio_system_make_payload_fields
          WHERE make_setting_id = ? \
          """
    params: List[Any] = [make_setting_id]
    if enabled is not None:
        if isinstance(enabled, bool):
            enabled = 1 if enabled else 0
        sql += " AND field_enabled = ?"
        params.append(enabled)
    return db.execute_query(sql, tuple(params), fetch_mode="all")


def add_or_update_system_make_field(db: SQLiteDatabase, data: dict):
    """
    Insert or update radio_system_make_payload_fields.

    INSERT path: omit `payload_field_id`
    UPDATE path: supply `payload_field_id`
    Required: make_setting_id OR radio_system_id  +  field_key, field_value
    """
    pfid = data.get("payload_field_id")
    make_setting_id = data.get("make_setting_id")
    radio_system_id = data.get("radio_system_id")
    key = (data.get("field_key") or "").strip()
    value_tpl = (data.get("field_value") or "").strip()
    enabled = _bool_like(data.get("field_enabled", 1))

    # Resolve make_setting_id from radio_system_id if needed
    if make_setting_id is None and radio_system_id:
        res = db.execute_query(
            "SELECT make_setting_id FROM radio_system_make_settings WHERE radio_system_id = ?",
            (radio_system_id,),
            fetch_mode="one",
        )
        if not res.get("success") or not res.get("result"):
            return {"success": False, "message": res.get("message", "Make settings row missing"), "result": []}
        make_setting_id = res["result"]["make_setting_id"]

    if not make_setting_id:
        return {"success": False, "message": "make_setting_id required", "result": []}

    # UPDATE
    if pfid:
        q = """
            UPDATE radio_system_make_payload_fields
            SET field_key = ?, field_value = ?, field_enabled = ?
            WHERE payload_field_id = ? \
            """
        return db.execute_commit(q, (key, value_tpl, enabled, pfid), return_count=True)

    # INSERT
    q = """
        INSERT INTO radio_system_make_payload_fields
            (make_setting_id, field_key, field_value, field_enabled)
        VALUES (?, ?, ?, ?) \
        """
    return db.execute_commit(q, (make_setting_id, key, value_tpl, enabled), return_row_id=True)


def delete_system_make_field(db: SQLiteDatabase, payload_field_id: int):
    """
    Delete a Make payload-field by primary key.
    """
    res = db.execute_commit(
        "DELETE FROM radio_system_make_payload_fields WHERE payload_field_id = ?",
        (payload_field_id,),
        return_row_id=False,
        return_count=True,
    )
    if not res["success"]:
        return {"success": False, "message": res["message"], "result": []}
    if res["result"] == 0:
        return {"success": False, "message": "Field not found.", "result": []}
    return {"success": True, "message": "Make payload field deleted.", "result": []}

# ===========================================================================
# N8N Settings
# ===========================================================================

def ensure_system_n8n_row(db: SQLiteDatabase, radio_system_id: int) -> dict:
    ins = db.execute_commit(
        """
        INSERT INTO radio_system_n8n_settings (radio_system_id)
        SELECT ?
        WHERE NOT EXISTS (
            SELECT 1 FROM radio_system_n8n_settings WHERE radio_system_id = ?
        )
        """,
        (radio_system_id, radio_system_id),
        return_row_id=False,
    )
    if not ins.get("success"):
        return {"success": False, "message": ins.get("message") or "Failed to ensure n8n row.", "result": []}
    return {"success": True, "message": "OK", "result": []}


def get_system_n8n_settings(db: SQLiteDatabase, radio_system_id: int) -> dict:
    """
    Return STANDARD system.n8n shape by calling get_systems(include_config=True)
    """
    ok = ensure_system_n8n_row(db, radio_system_id)
    if not ok.get("success"):
        return ok

    sys = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    if not sys.get("success") or not sys.get("result"):
        return {"success": False, "message": "System not found.", "result": []}

    n8n = (sys["result"][0].get("n8n") or {})
    return {"success": True, "message": "n8n settings retrieved.", "result": n8n}


def update_system_n8n_settings(db: SQLiteDatabase, data: dict) -> dict:
    """
    PATCH-style upsert for radio_system_n8n_settings using DB column keys.

    Accepts:
      - radio_system_id (required)
      - either:
          data["n8n"] = { n8n_enabled, n8n_webhook_url, n8n_timeout_s, jwt_passphrase, jwt_issuer,
                          jwt_audience, jwt_subject_template, jwt_ttl_s }
        OR flat keys at top-level with the same names.

    Returns:
      { success, message, result: <STANDARD system.n8n dict> }
    """

    module_logger.debug("Updating system n8n settings, %s", data)

    def _int_or_none(v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ValueError("must be an integer")

    def _str_or_none(v):
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return None if v == "" else v
        # if someone sends non-string here, coerce to string
        v = str(v).strip()
        return None if v == "" else v

    # ---- required ---------------------------------------------------------
    rsid = data.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id is required.", "result": []}
    try:
        rsid = int(rsid)
    except (TypeError, ValueError):
        return {"success": False, "message": "radio_system_id must be an integer.", "result": []}

    # ---- pull payload (nested or flat) ------------------------------------
    src = data.get("n8n")
    if not isinstance(src, dict):
        src = dict(data or {})

    allowed = {
        "n8n_enabled",
        "n8n_webhook_url",
        "n8n_timeout_s",
        "jwt_passphrase",
        "jwt_issuer",
        "jwt_audience",
        "jwt_subject_template",
        "jwt_ttl_s",
    }

    # drop noise like _csrf_token, system_name, radio_system_id, etc.
    src = {k: v for k, v in (src or {}).items() if k in allowed}

    # ---- ensure base row exists ------------------------------------------
    ensure = ensure_system_n8n_row(db, rsid)
    if not ensure.get("success"):
        return ensure

    # ---- load current for merge/validation --------------------------------
    cur = db.execute_query(
        "SELECT * FROM radio_system_n8n_settings WHERE radio_system_id = ?",
        (rsid,),
        fetch_mode="one",
    )
    if not cur.get("success") or not cur.get("result"):
        return {"success": False, "message": "Failed to load current n8n settings.", "result": []}
    current = cur["result"]

    # ---- build patch updates (DB columns) ---------------------------------
    norm: dict[str, object] = {}

    if "n8n_enabled" in src:
        # accept 0/1, "0"/"1", true/false-ish
        norm["n8n_enabled"] = _bool_like(src.get("n8n_enabled"))

    if "n8n_webhook_url" in src:
        norm["n8n_webhook_url"] = _str_or_none(src.get("n8n_webhook_url"))

    if "jwt_passphrase" in src:
        norm["jwt_passphrase"] = _str_or_none(src.get("jwt_passphrase"))

    if "jwt_issuer" in src:
        norm["jwt_issuer"] = _str_or_none(src.get("jwt_issuer"))

    if "jwt_audience" in src:
        norm["jwt_audience"] = _str_or_none(src.get("jwt_audience"))

    if "jwt_subject_template" in src:
        norm["jwt_subject_template"] = _str_or_none(src.get("jwt_subject_template"))

    if "n8n_timeout_s" in src:
        try:
            v = _int_or_none(src.get("n8n_timeout_s"))
        except ValueError:
            return {"success": False, "message": "n8n_timeout_s must be an integer.", "result": []}
        if v is not None:
            if v < 1 or v > 120:
                return {"success": False, "message": "n8n_timeout_s must be between 1 and 120.", "result": []}
            norm["n8n_timeout_s"] = v

    if "jwt_ttl_s" in src:
        try:
            v = _int_or_none(src.get("jwt_ttl_s"))
        except ValueError:
            return {"success": False, "message": "jwt_ttl_s must be an integer.", "result": []}
        if v is not None:
            if v < 30 or v > 86400:
                return {"success": False, "message": "jwt_ttl_s must be between 30 and 86400.", "result": []}
            norm["jwt_ttl_s"] = v

    if not norm:
        return get_system_n8n_settings(db, rsid)

    # ---- validate final merged state when enabled -------------------------
    merged = {**current, **norm}
    if int(merged.get("n8n_enabled") or 0) == 1:
        if not merged.get("n8n_webhook_url") or not merged.get("jwt_passphrase"):
            return {
                "success": False,
                "message": "Cannot enable n8n without both n8n_webhook_url and jwt_passphrase.",
                "result": [],
            }

    # ---- apply update -----------------------------------------------------
    sets = ", ".join(f"{k} = ?" for k in norm.keys())
    params = list(norm.values()) + [rsid]
    q = f"UPDATE radio_system_n8n_settings SET {sets} WHERE radio_system_id = ?"

    upd = db.execute_commit(q, tuple(params), return_count=True)
    if not upd.get("success"):
        return {"success": False, "message": upd.get("message") or "Update failed.", "result": []}

    return get_system_n8n_settings(db, rsid)

# ===========================================================================
# Pushover
# ===========================================================================
def update_system_pushover_settings(db: SQLiteDatabase, system_data: dict):
    """
    Update Pushover settings for a system.

    Expected keys:
      - radio_system_id (required)
      - pushover_enabled (0/1/bool)
      - pushover_group_token (str|None)
      - pushover_app_token   (str|None)
      - pushover_body        (str|default)
      - pushover_subject     (str|default)
      - pushover_sound       (str|default)
    """
    q = """
        UPDATE radio_system_pushover_settings
        SET pushover_enabled   = ?,
            pushover_group_token = ?,
            pushover_app_token   = ?,
            pushover_body        = ?,
            pushover_subject     = ?,
            pushover_sound       = ?
        WHERE radio_system_id = ? \
        """
    params = (
        _bool_like(system_data.get("pushover_enabled", 0)),
        system_data.get("pushover_group_token") or None,
        system_data.get("pushover_app_token") or None,
        system_data.get("pushover_body")
        or '<font color="red"><b>{trigger_name}</b></font><br><br>'
           '<a href="{audio_url}">Click for Dispatch Audio</a><br><br>'
           '<a href="{stream_url}">Click Audio Stream</a>',
        system_data.get("pushover_subject") or "Dispatch Alert",
        system_data.get("pushover_sound") or "pushover",
        system_data.get("radio_system_id"),
    )
    try:
        return db.execute_commit(q, params, return_count=True)
    except Exception as e:
        module_logger.error("Unexpected Exception when saving system pushover settings: %s", e)
        return {"success": False, "message": f"Unexpected Exception when saving system pushover settings: {e}", "result": []}


# ===========================================================================
# Email settings & recipients
# ===========================================================================
def update_system_email_settings(db: SQLiteDatabase, payload: dict) -> dict:
    """
    Update the SMTP / alert-template section for one radio system.

    Required:
      - radio_system_id

    Optional keys:
      email_enabled (bool/int/str), smtp_hostname, smtp_port, smtp_username, smtp_password,
      email_address_from, email_text_from, email_alert_subject, email_alert_body
    """
    try:
        sys_id = int(payload.get("radio_system_id", 0))
    except ValueError:
        return {"success": False, "message": "radio_system_id must be an integer.", "result": []}
    if not sys_id:
        return {"success": False, "message": "radio_system_id is required.", "result": []}

    email_enabled = _bool_like(payload.get("email_enabled", 0))
    smtp_hostname = payload.get("smtp_hostname") or None
    smtp_port = int(payload["smtp_port"]) if payload.get("smtp_port") else None
    smtp_username = payload.get("smtp_username") or None
    smtp_password = payload.get("smtp_password") or None
    email_address_from = payload.get("email_address_from") or "dispatch@example.com"
    email_text_from = payload.get("email_text_from") or "iCAD Dispatch"
    email_alert_subject = payload.get("email_alert_subject") or "Dispatch Alert"
    email_alert_body = payload.get(
        "email_alert_body",
        "{trigger_list} Alert at {timestamp_24}<br><br>{transcript}"
        '<br><br><a href="{audio_url}">Click for Dispatch Audio</a>'
        '<br><br><a href="{stream_url}">Click Audio Stream</a>',
    )

    try:
        res = db.execute_commit(
            """
            UPDATE radio_system_email_settings
            SET email_enabled       = ?,
                smtp_hostname       = ?,
                smtp_port           = ?,
                smtp_username       = ?,
                smtp_password       = ?,
                email_address_from  = ?,
                email_text_from     = ?,
                email_alert_subject = ?,
                email_alert_body    = ?
            WHERE radio_system_id = ?
            """,
            (
                email_enabled,
                smtp_hostname,
                smtp_port,
                smtp_username,
                smtp_password,
                email_address_from,
                email_text_from,
                email_alert_subject,
                email_alert_body,
                sys_id,
            ),
            return_count=True,
        )
    except Exception as e:
        module_logger.error("Exception updating email settings for system %s: %s", sys_id, e)
        return {"success": False, "message": f"Unexpected DB exception: {e}", "result": []}

    if not res["success"]:
        return {"success": False, "message": f"DB error: {res['message']}", "result": []}
    return {"success": True, "message": "Email settings saved.", "result": []}


def get_system_emails(
        db: SQLiteDatabase,
        email_id: int | None = None,
        email_address: str | None = None,
        radio_system_id: int | None = None,
        system_decimal: int | None = None,
        enabled: int | bool | None = None,
):
    """
    Retrieve email recipient rows, optionally joined/filtering by the parent system.
    """
    base = """
           SELECT re.email_id,
                  re.radio_system_id,
                  re.email_address,
                  re.enabled,
                  rs.system_decimal
           FROM radio_system_emails AS re
                    JOIN radio_systems AS rs ON re.radio_system_id = rs.radio_system_id \
           """
    where: List[str] = []
    params: List[Any] = []

    if email_id is not None:
        where.append("re.email_id = ?")
        params.append(email_id)
    if email_address is not None:
        where.append("re.email_address = ?")
        params.append(email_address)
    if radio_system_id is not None:
        where.append("re.radio_system_id = ?")
        params.append(radio_system_id)
    if system_decimal is not None:
        where.append("rs.system_decimal = ?")
        params.append(system_decimal)
    if enabled is not None:
        enabled_val = 1 if (enabled is True or enabled == 1) else 0
        where.append("re.enabled = ?")
        params.append(enabled_val)

    q = base + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY re.email_id ASC"
    return db.execute_query(q, tuple(params), fetch_mode="all")


def add_or_update_system_email(db: SQLiteDatabase, email_data: dict):
    """
    Insert or update a recipient in radio_system_emails.

    UPDATE: pass email_id.
    INSERT: omit email_id.
    Required fields: radio_system_id, email_address, enabled (bool/int/str).
    """
    email_id = email_data.get("email_id")
    radio_system_id = email_data.get("radio_system_id")
    email_address = email_data.get("email_address")
    enabled = email_data.get("enabled")

    if radio_system_id is None or email_address is None or enabled is None:
        return {
            "success": False,
            "message": "Missing required fields: radio_system_id, email_address, enabled.",
            "result": [],
        }

    # normalize enabled
    if isinstance(enabled, bool):
        enabled = 1 if enabled else 0
    elif isinstance(enabled, str):
        enabled = enabled.strip().lower()
        if enabled in {"true", "1"}:
            enabled = 1
        elif enabled in {"false", "0"}:
            enabled = 0
        else:
            return {"success": False, "message": "Invalid value for enabled", "result": []}
    elif isinstance(enabled, (int, float)):
        if enabled not in (0, 1):
            return {"success": False, "message": "Invalid value for enabled", "result": []}
        enabled = int(enabled)
    else:
        return {"success": False, "message": "Invalid type for enabled", "result": []}

    if email_id is not None:
        q = """
            UPDATE radio_system_emails
            SET email_address = ?, enabled = ?
            WHERE email_id = ? \
            """
        return db.execute_commit(q, (email_address, enabled, email_id), return_count=True)

    q = "INSERT INTO radio_system_emails (radio_system_id, email_address, enabled) VALUES (?, ?, ?)"
    return db.execute_commit(q, (radio_system_id, email_address, enabled), return_row_id=True)


def delete_system_email(
        db: SQLiteDatabase,
        email_id: int | None = None,
        email_address: str | None = None,
        radio_system_id: int | None = None,
):
    """
    Delete a recipient from radio_system_emails.

    Preferred: email_id.
    Alternate: (email_address + radio_system_id).
    """
    if email_id is None and email_address is None:
        return {"success": False, "message": "email_id or email_address required.", "result": []}
    if email_address and radio_system_id is None:
        return {"success": False, "message": "radio_system_id required when deleting by email_address.", "result": []}

    # Confirm existence
    if email_id is not None:
        sel_q, sel_p = "SELECT email_id FROM radio_system_emails WHERE email_id = ?", (email_id,)
    else:
        sel_q, sel_p = (
            "SELECT email_id FROM radio_system_emails WHERE email_address = ? AND radio_system_id = ?",
            (email_address, radio_system_id),
        )
    sel = db.execute_query(sel_q, sel_p, fetch_mode="one")
    if not sel.get("success"):
        return {"success": False, "message": f"Error checking email: {sel.get('message')}", "result": []}
    if not sel.get("result"):
        return {"success": False, "message": "Email address does not exist.", "result": []}

    # Delete
    if email_id is not None:
        del_q, del_p = "DELETE FROM radio_system_emails WHERE email_id = ?", (email_id,)
    else:
        del_q, del_p = (
            "DELETE FROM radio_system_emails WHERE email_address = ? AND radio_system_id = ?",
            (email_address, radio_system_id),
        )
    del_res = db.execute_commit(del_q, del_p, return_count=True, return_row_id=False)
    if not del_res.get("success"):
        return {"success": False, "message": f"Error deleting email address: {del_res.get('message')}", "result": []}
    if del_res.get("result", 0) == 0:
        return {"success": False, "message": "No email address deleted. It may no longer exist.", "result": []}
    return {"success": True, "message": "Email address deleted successfully.", "result": []}


# ===========================================================================
# Transcribe
# ===========================================================================
def update_system_transcribe_settings(db, data: dict) -> dict:
    """
    Upsert and update only provided keys.
    Required: radio_system_id

    Optional (normalized here):
      - transcribe_enabled  → _bool_like(...)
      - transcribe_url      → _norm_str(...); '' → None
      - transcribe_api_key  → _norm_str(...); '' → None
      - transcribe_model    → _norm_str(...); '' → None
      - transcribe_language → _norm_str(...); '' → None
      - transcribe_prompt   → _norm_str(...); '' → None
    """
    rsid = data.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id is required."}

    # ensure row exists
    ensure = db.execute_commit(
        "INSERT INTO radio_system_transcribe_settings (radio_system_id) "
        "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM radio_system_transcribe_settings WHERE radio_system_id=?)",
        (rsid, rsid), return_row_id=False
    )
    if not ensure["success"]:
        return {"success": False, "message": ensure["message"]}

    # normalize using existing helpers
    norm: Dict[str, Any] = {}
    if "transcribe_enabled" in data:
        norm["transcribe_enabled"] = _bool_like(data.get("transcribe_enabled"))

    for key in ("transcribe_url", "transcribe_api_key",
                "transcribe_model", "transcribe_language", "transcribe_prompt"):
        if key in data:
            v = _norm_str(data.get(key))
            norm[key] = None if (v is None or v == "") else v

    if not norm:
        return {"success": True, "message": "No changes.", "result": []}

    sets = ", ".join(f"{k} = ?" for k in norm.keys())
    params = list(norm.values()) + [rsid]
    q = f"UPDATE radio_system_transcribe_settings SET {sets} WHERE radio_system_id = ?"
    upd = db.execute_commit(q, tuple(params), return_row_id=False)
    if not upd["success"]:
        return {"success": False, "message": upd["message"]}
    return {"success": True, "message": "Updated.", "result": []}


# ===========================================================================
# Upload / Split-Dispatch
# ===========================================================================
def update_system_upload_settings(db: SQLiteDatabase, payload: dict) -> dict:
    """
    PATCH helper for /upload/settings.
    Updates only the provided keys from:
      {split_enabled, tail_min_voice_sec, vad_min_speech_ratio,
       voice_rms_dbfs, max_split_interval, max_split_length}
    """
    rsid = payload.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id missing", "result": []}

    allowed = {
        "split_enabled", "tail_min_voice_sec", "vad_min_speech_ratio",
        "voice_rms_dbfs", "max_split_interval", "max_split_length", "audio_min_length"
    }

    sets, params = [], []
    for k, v in payload.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v if k != "split_enabled" else _bool_like(v))

    if not sets:
        return {"success": True, "message": "Nothing to update", "result": []}

    params.append(rsid)
    sql = f"UPDATE radio_system_upload_settings SET {', '.join(sets)} WHERE radio_system_id = ?"
    return db.execute_commit(sql, params, return_row_id=False, return_count=True)

# ===========================================================================
# Address Extraction Settings
# ===========================================================================
def update_system_address_extraction_settings(db: SQLiteDatabase, data: dict) -> dict:
    """
    Upsert address extraction settings for a radio system.

    Required:
      - radio_system_id

    Optional:
      - address_extraction_enabled (bool/int/str)
      - openai_api_key (str)
      - openai_model (str)
      - google_maps_api_key (str)
      - geocode_country (str)
      - geocode_state (str)
      - geocode_city (str)
    """
    rsid = data.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id is required.", "result": []}

    # Ensure row exists
    ensure = db.execute_commit(
        """
        INSERT INTO radio_system_address_extraction_settings (radio_system_id)
        SELECT ? WHERE NOT EXISTS (
            SELECT 1 FROM radio_system_address_extraction_settings
            WHERE radio_system_id = ?
        )
        """,
        (rsid, rsid),
        return_row_id=False
    )
    if not ensure["success"]:
        return {"success": False, "message": ensure["message"], "result": []}

    norm: Dict[str, Any] = {}

    # --- Handle address_extraction_enabled (and validation when enabling) ---
    if "address_extraction_enabled" in data:
        raw_enabled = data.get("address_extraction_enabled")
        enabled_bool = _bool_like(raw_enabled)  # your helper, assumed to return True/False

        if enabled_bool:
            # Only require keys when turning ON
            current = db.execute_query(
                """
                SELECT openai_api_key, google_maps_api_key
                FROM radio_system_address_extraction_settings
                WHERE radio_system_id = ?
                """,
                (rsid,),
                fetch_mode="one"
            )

            openai_key = data.get("openai_api_key") or (
                current.get("result", {}).get("openai_api_key") if current.get("success") else None
            )
            google_key = data.get("google_maps_api_key") or (
                current.get("result", {}).get("google_maps_api_key") if current.get("success") else None
            )

            if not openai_key or not google_key:
                return {
                    "success": False,
                    "message": "Cannot enable address extraction without both openai_api_key and google_maps_api_key",
                    "result": []
                }

        # Always update the column if it was provided in data
        norm["address_extraction_enabled"] = 1 if enabled_bool else 0

    # --- Handle the other string fields ---
    for key in (
            "openai_api_key", "openai_model", "google_maps_api_key",
            "geocode_country", "geocode_state", "geocode_city"
    ):
        if key in data:
            v = _norm_str(data.get(key))
            if v is not None:
                norm[key] = v

    if not norm:
        return {"success": True, "message": "No changes to apply.", "result": []}

    sets = ", ".join(f"{k} = ?" for k in norm.keys())
    params = list(norm.values()) + [rsid]
    q = f"UPDATE radio_system_address_extraction_settings SET {sets} WHERE radio_system_id = ?"

    try:
        upd = db.execute_commit(q, tuple(params), return_count=True)
        if not upd["success"]:
            return {"success": False, "message": upd["message"], "result": []}
        return {"success": True, "message": "Address extraction settings updated.", "result": []}
    except Exception as e:
        module_logger.error("Error updating address extraction settings: %s", e)
        return {"success": False, "message": f"Unexpected error: {e}", "result": []}


def get_system_address_extraction_settings(
        db: SQLiteDatabase,
        radio_system_id: int | None = None,
        system_decimal: int | None = None,
        include_regions: bool = False,
) -> dict:
    """
    Retrieve address extraction settings for one or all systems.

    Parameters
    ----------
    radio_system_id : int | None
        Filter by specific system ID
    system_decimal : int | None
        Filter by system decimal
    include_regions : bool
        If True, include geocoding regions in nested structure

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,
          "result": dict | list[dict]
        }
    """
    base = """
           SELECT
               aes.address_extraction_setting_id,
               aes.radio_system_id,
               aes.address_extraction_enabled,
               aes.openai_api_key,
               aes.openai_model,
               aes.google_maps_api_key,
               aes.geocode_country,
               aes.geocode_state,
               aes.geocode_city,
               rs.system_decimal,
               rs.system_name
           FROM radio_system_address_extraction_settings AS aes
                    JOIN radio_systems AS rs ON aes.radio_system_id = rs.radio_system_id \
           """

    conditions: List[str] = []
    params: List[Any] = []

    if radio_system_id is not None:
        conditions.append("aes.radio_system_id = ?")
        params.append(radio_system_id)

    if system_decimal is not None:
        conditions.append("rs.system_decimal = ?")
        params.append(system_decimal)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = base + where + " ORDER BY rs.system_name"

    res = db.execute_query(query, tuple(params), fetch_mode="all" if not radio_system_id else "one")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}

    rows = res["result"]
    if not rows:
        return {"success": True, "message": "No settings found.", "result": [] if not radio_system_id else None}

    # If single system query, convert to list for processing
    single_result = False
    if not isinstance(rows, list):
        rows = [rows]
        single_result = True

    results = []
    for row in rows:
        setting = {
            "address_extraction_setting_id": row["address_extraction_setting_id"],
            "radio_system_id": row["radio_system_id"],
            "system_decimal": row["system_decimal"],
            "system_name": row["system_name"],
            "address_extraction_enabled": row["address_extraction_enabled"],
            "openai_api_key": row["openai_api_key"],
            "openai_model": row["openai_model"],
            "google_maps_api_key": row["google_maps_api_key"],
            "geocode_country": row["geocode_country"],
            "geocode_state": row["geocode_state"],
            "geocode_city": row["geocode_city"],
        }

        if include_regions:
            setting["regions"] = []
            regions_res = get_geocoding_regions(
                db,
                address_extraction_setting_id=row["address_extraction_setting_id"]
            )
            if regions_res.get("success"):
                setting["regions"] = [
                    {
                        "region_id": r["region_id"],
                        "state_code": r["state_code"],
                        "county_name": r["county_name"],
                        "priority": r["priority"],
                    }
                    for r in regions_res["result"]
                ]

        results.append(setting)

    return {
        "success": True,
        "message": "Settings retrieved successfully.",
        "result": results[0] if single_result else results
    }


# ===========================================================================
# Geocoding Regions
# ===========================================================================
def get_geocoding_regions(
        db: SQLiteDatabase,
        region_id: int | None = None,
        address_extraction_setting_id: int | None = None,
        radio_system_id: int | None = None,
        state_code: str | None = None,
) -> dict:
    """
    Retrieve geocoding regions with optional filters.

    Parameters
    ----------
    region_id : int | None
        Specific region ID
    address_extraction_setting_id : int | None
        Filter by settings ID
    radio_system_id : int | None
        Filter by radio system (joins to get setting_id)
    state_code : str | None
        Filter by state code
    """
    base = """
           SELECT
               gr.region_id,
               gr.address_extraction_setting_id,
               gr.state_code,
               gr.county_name,
               gr.priority
           FROM geocoding_regions AS gr \
           """

    conditions: List[str] = []
    params: List[Any] = []

    if region_id is not None:
        conditions.append("gr.region_id = ?")
        params.append(region_id)

    if address_extraction_setting_id is not None:
        conditions.append("gr.address_extraction_setting_id = ?")
        params.append(address_extraction_setting_id)

    if radio_system_id is not None:
        base += " JOIN radio_system_address_extraction_settings aes ON gr.address_extraction_setting_id = aes.address_extraction_setting_id"
        conditions.append("aes.radio_system_id = ?")
        params.append(radio_system_id)

    if state_code is not None:
        conditions.append("gr.state_code = ?")
        params.append(state_code)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = base + where + " ORDER BY gr.priority ASC, gr.state_code, gr.county_name"

    return db.execute_query(query, tuple(params), fetch_mode="all")


def add_or_update_geocoding_region(db: SQLiteDatabase, data: dict) -> dict:
    """
    Insert or update a geocoding region.

    UPDATE path: pass 'region_id'
    INSERT path: omit 'region_id'

    Required (for insert):
      - address_extraction_setting_id OR radio_system_id
      - state_code
      - county_name

    Optional:
      - priority (int; default 0)
    """
    region_id = data.get("region_id")
    aes_id = data.get("address_extraction_setting_id")
    radio_system_id = data.get("radio_system_id")
    state_code = (data.get("state_code") or "").strip().upper()
    county_name = (data.get("county_name") or "").strip()
    priority = int(data.get("priority", 0))

    # UPDATE
    if region_id is not None:
        if not state_code or not county_name:
            return {"success": False, "message": "state_code and county_name required.", "result": []}

        q = """
            UPDATE geocoding_regions
            SET state_code = ?, county_name = ?, priority = ?
            WHERE region_id = ? \
            """
        return db.execute_commit(q, (state_code, county_name, priority, region_id), return_count=True)

    # INSERT - resolve address_extraction_setting_id if needed
    if aes_id is None:
        if radio_system_id is None:
            return {"success": False, "message": "address_extraction_setting_id or radio_system_id required.", "result": []}

        res = db.execute_query(
            "SELECT address_extraction_setting_id FROM radio_system_address_extraction_settings WHERE radio_system_id = ?",
            (radio_system_id,),
            fetch_mode="one"
        )
        if not res.get("success") or not res.get("result"):
            return {"success": False, "message": "Address extraction settings not found for this system.", "result": []}
        aes_id = res["result"]["address_extraction_setting_id"]

    if not state_code or not county_name:
        return {"success": False, "message": "state_code and county_name are required.", "result": []}

    # Check for duplicate
    dup = db.execute_query(
        """
        SELECT region_id FROM geocoding_regions
        WHERE address_extraction_setting_id = ?
          AND state_code = ?
          AND county_name = ?
        """,
        (aes_id, state_code, county_name),
        fetch_mode="one"
    )
    if dup.get("success") and dup.get("result"):
        return {"success": False, "message": "Region already exists for this state/county combination.", "result": []}

    q = """
        INSERT INTO geocoding_regions
            (address_extraction_setting_id, state_code, county_name, priority)
        VALUES (?, ?, ?, ?) \
        """
    return db.execute_commit(q, (aes_id, state_code, county_name, priority), return_row_id=True)


def delete_geocoding_region(
        db: SQLiteDatabase,
        region_id: int | None = None,
        address_extraction_setting_id: int | None = None,
        state_code: str | None = None,
        county_name: str | None = None,
) -> dict:
    """
    Delete a geocoding region.

    Preferred: region_id
    Alternate: (address_extraction_setting_id + state_code + county_name)
    """
    if region_id is None and (address_extraction_setting_id is None or not state_code or not county_name):
        return {
            "success": False,
            "message": "region_id OR (address_extraction_setting_id + state_code + county_name) required.",
            "result": []
        }

    # Find region to delete
    if region_id is not None:
        sel_q = "SELECT region_id FROM geocoding_regions WHERE region_id = ?"
        sel_params = (region_id,)
    else:
        sel_q = """
                SELECT region_id FROM geocoding_regions
                WHERE address_extraction_setting_id = ?
                  AND state_code = ?
                  AND county_name = ? \
                """
        sel_params = (address_extraction_setting_id, state_code.upper(), county_name)

    sel_res = db.execute_query(sel_q, sel_params, fetch_mode="one")
    if not sel_res.get("success"):
        return {"success": False, "message": sel_res.get("message"), "result": []}
    if not sel_res.get("result"):
        return {"success": False, "message": "Region not found.", "result": []}

    rid = sel_res["result"]["region_id"]

    # Delete
    del_res = db.execute_commit(
        "DELETE FROM geocoding_regions WHERE region_id = ?",
        (rid,),
        return_count=True,
        return_row_id=False
    )
    if not del_res.get("success"):
        return {"success": False, "message": del_res.get("message"), "result": []}
    if del_res.get("result", 0) == 0:
        return {"success": False, "message": "No region deleted.", "result": []}

    return {"success": True, "message": "Geocoding region deleted.", "result": []}


def reorder_geocoding_regions(
        db: SQLiteDatabase,
        address_extraction_setting_id: int,
        ordered_ids: list[int]
) -> dict:
    """
    Update priority for geocoding regions (top→bottom as 10, 20, 30...).

    Parameters
    ----------
    address_extraction_setting_id : int
        The settings record these regions belong to
    ordered_ids : list[int]
        List of region_id values in desired order (first = highest priority)
    """
    if not ordered_ids:
        return {"success": False, "message": "No region IDs provided.", "result": []}

    # Build CASE expression
    case_parts: List[str] = []
    params: List[Any] = []
    for idx, rid in enumerate(ordered_ids):
        priority_val = (idx + 1) * 10
        case_parts.append("WHEN ? THEN ?")
        params.extend([rid, priority_val])

    placeholders = ",".join("?" for _ in ordered_ids)
    case_sql = " ".join(case_parts)
    q = f"""
        UPDATE geocoding_regions
        SET priority = CASE region_id {case_sql} ELSE priority END
        WHERE address_extraction_setting_id = ?
        AND region_id IN ({placeholders})
    """
    params.append(address_extraction_setting_id)
    params.extend(ordered_ids)

    return db.execute_commit(q, tuple(params), return_count=True)


def bulk_add_geocoding_regions(
        db: SQLiteDatabase,
        address_extraction_setting_id: int,
        regions: list[dict]
) -> dict:
    """
    Bulk insert multiple geocoding regions in a single transaction.

    Parameters
    ----------
    address_extraction_setting_id : int
        The settings record to attach regions to
    regions : list[dict]
        List of dicts with keys: state_code, county_name, priority (optional)
        Example: [
            {"state_code": "PA", "county_name": "Bradford", "priority": 10},
            {"state_code": "NY", "county_name": "Tioga", "priority": 20}
        ]
    """
    if not regions:
        return {"success": False, "message": "No regions provided.", "result": []}

    try:
        db.begin()

        added = 0
        for idx, region in enumerate(regions):
            state = (region.get("state_code") or "").strip().upper()
            county = (region.get("county_name") or "").strip()
            priority = int(region.get("priority", (idx + 1) * 10))

            if not state or not county:
                module_logger.warning("Skipping invalid region: %s", region)
                continue

            # Check for duplicate
            dup = db.execute_query(
                """
                SELECT region_id FROM geocoding_regions
                WHERE address_extraction_setting_id = ?
                  AND state_code = ?
                  AND county_name = ?
                """,
                (address_extraction_setting_id, state, county),
                fetch_mode="one"
            )
            if dup.get("success") and dup.get("result"):
                module_logger.warning("Region %s:%s already exists, skipping", state, county)
                continue

            res = db.execute_commit(
                """
                INSERT INTO geocoding_regions
                    (address_extraction_setting_id, state_code, county_name, priority)
                VALUES (?, ?, ?, ?)
                """,
                (address_extraction_setting_id, state, county, priority),
                return_row_id=True
            )
            if not res["success"]:
                raise RuntimeError(f"Failed to insert {state}:{county}: {res['message']}")
            added += 1

        db.commit()
        return {
            "success": True,
            "message": f"Added {added} region(s).",
            "result": added
        }

    except Exception as e:
        module_logger.error("bulk_add_geocoding_regions() rolled back: %s", e)
        db.rollback()
        return {"success": False, "message": str(e), "result": 0}

# ===========================================================================
# Storage Settings (LOCAL / SFTP / S3)
# ===========================================================================
def update_system_storage_settings(db: SQLiteDatabase, data: dict) -> dict:
    """
    Upsert storage settings for a radio system.

    Required:
      - radio_system_id

    Optional:
      - storage_type: 'LOCAL' | 'SFTP' | 'S3'
      - path_pattern: str (e.g. "%Y/%m/%d")

      SFTP/SCP:
        - sftp_host, sftp_port, sftp_username, sftp_password, sftp_use_ssh_key,
          sftp_remote_path, sftp_base_url, sftp_timeout_s, sftp_max_retries

      S3:
        - s3_bucket, s3_region, s3_access_key_id, s3_secret_access_key,
          s3_endpoint_url, s3_object_key_prefix
    """
    rsid = data.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id is required.", "result": []}

    # Ensure base row exists (defaults: LOCAL + default path_pattern)
    ensure = db.execute_commit(
        """
        INSERT INTO radio_system_storage_settings (radio_system_id)
        SELECT ? WHERE NOT EXISTS (
            SELECT 1 FROM radio_system_storage_settings WHERE radio_system_id = ?
        )
        """,
        (rsid, rsid),
        return_row_id=False,
    )
    if not ensure["success"]:
        return {"success": False, "message": ensure["message"], "result": []}

    # Get current row for validation/merging
    cur = db.execute_query(
        "SELECT * FROM radio_system_storage_settings WHERE radio_system_id = ?",
        (rsid,),
        fetch_mode="one",
    )
    if not cur.get("success") or not cur.get("result"):
        return {"success": False, "message": "Failed to load current storage settings.", "result": []}
    current = cur["result"]

    norm: Dict[str, Any] = {}

    # --- storage_type (with validation) ---
    if "storage_type" in data:
        st_raw = (data.get("storage_type") or "").strip().upper()
        if st_raw not in {"LOCAL", "SFTP", "S3"}:
            return {"success": False, "message": "storage_type must be one of LOCAL, SFTP, S3", "result": []}
        norm["storage_type"] = st_raw
    else:
        st_raw = current.get("storage_type", "LOCAL")

    # --- path_pattern ---
    if "path_pattern" in data:
        v = _norm_str(data.get("path_pattern"))
        norm["path_pattern"] = v or "%Y/%m/%d"

    # --- SFTP fields ---
    def _int_or_default(key: str, default: int) -> int:
        val = data.get(key, None)
        if val in (None, ""):
            return current.get(key, default) if current.get(key) is not None else default
        try:
            return int(val)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer")

    for key in (
            "sftp_host",
            "sftp_username",
            "sftp_password",
            "sftp_use_ssh_key",
            "sftp_remote_path",
            "sftp_base_url",
    ):
        if key in data:
            v = _norm_str(data.get(key))
            norm[key] = v

    # numeric SFTP fields
    for key, default in (
            ("sftp_port", 22),
            ("sftp_timeout_s", 30),
            ("sftp_max_retries", 3),
            ("sftp_use_ssh_key", 0)
    ):
        if key in data:
            try:
                norm[key] = _int_or_default(key, default)
            except ValueError as e:
                return {"success": False, "message": str(e), "result": []}

    # --- S3 fields ---
    for key in (
            "s3_bucket",
            "s3_region",
            "s3_access_key_id",
            "s3_secret_access_key",
            "s3_endpoint_url",
            "s3_object_key_prefix",
    ):
        if key in data:
            v = _norm_str(data.get(key))
            norm[key] = v

    # --- Validation when switching to SFTP / S3 ---
    # Merge to see "final" values for required fields
    merged = {**current, **norm}

    if st_raw == "SFTP":
        if not merged.get("sftp_host") or not merged.get("sftp_remote_path") or not merged.get("sftp_base_url"):
            return {
                "success": False,
                "message": "SFTP storage requires sftp_host, sftp_remote_path, and sftp_base_url.",
                "result": [],
            }

    if st_raw == "S3":
        if not merged.get("s3_bucket"):
            return {
                "success": False,
                "message": "S3 storage requires s3_bucket.",
                "result": [],
            }

    if not norm:
        return {"success": True, "message": "No changes to apply.", "result": []}

    sets = ", ".join(f"{k} = ?" for k in norm.keys())
    params = list(norm.values()) + [rsid]
    q = f"UPDATE radio_system_storage_settings SET {sets} WHERE radio_system_id = ?"

    try:
        upd = db.execute_commit(q, tuple(params), return_count=True)
        if not upd["success"]:
            return {"success": False, "message": upd["message"], "result": []}
        return {"success": True, "message": "Storage settings updated.", "result": []}
    except Exception as e:
        module_logger.error("Error updating storage settings: %s", e)
        return {"success": False, "message": f"Unexpected error: {e}", "result": []}

def get_system_storage_settings(
        db: SQLiteDatabase,
        radio_system_id: int | None = None
) -> dict:
    """
    Retrieve storage settings for one or all systems.

    Parameters
    ----------
    radio_system_id : int | None
        Filter by specific system ID

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,
          "result": dict | list[dict]
        }
    """
    base = """
           SELECT
               st.radio_system_id,
               rs.system_name,

               st.storage_type,
               st.path_pattern,

               st.sftp_host,
               st.sftp_port,
               st.sftp_username,
               st.sftp_password,
               st.sftp_use_ssh_key,
               st.sftp_remote_path,
               st.sftp_base_url,
               st.sftp_timeout_s,
               st.sftp_max_retries,

               st.s3_bucket,
               st.s3_region,
               st.s3_access_key_id,
               st.s3_secret_access_key,
               st.s3_endpoint_url,
               st.s3_object_key_prefix
           FROM radio_system_storage_settings AS st
                    JOIN radio_systems AS rs ON st.radio_system_id = rs.radio_system_id \
           """

    conditions: List[Any] = []
    params: List[Any] = []

    if radio_system_id is not None:
        conditions.append("st.radio_system_id = ?")
        params.append(radio_system_id)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = base + where + " ORDER BY rs.system_name"

    res = db.execute_query(query, tuple(params), fetch_mode="all" if not radio_system_id else "one")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}

    rows = res["result"]
    if not rows:
        return {"success": True, "message": "No settings found.", "result": [] if not radio_system_id else None}

    single_result = False
    if not isinstance(rows, list):
        rows = [rows]
        single_result = True

    results = []
    for row in rows:
        key_path = SSH_KEY_ROOT / str(row["radio_system_id"]) / "id_rsa"
        ssh_key_exists = key_path.is_file()

        cfg = {
            "radio_system_id": row["radio_system_id"],
            "system_name": row["system_name"],
            "storage_type": row["storage_type"] or "LOCAL",
            "path_pattern": row["path_pattern"] or "%Y/%m/%d",
            "sftp": {
                "host": row["sftp_host"],
                "port": row["sftp_port"],
                "username": row["sftp_username"],
                "password": row["sftp_password"],
                "use_ssh_key": row["sftp_use_ssh_key"],
                "ssh_key_exists": ssh_key_exists,
                "remote_path": row["sftp_remote_path"],
                "base_url": row["sftp_base_url"],
                "timeout_s": row["sftp_timeout_s"],
                "max_retries": row["sftp_max_retries"],
            },
            "s3": {
                "bucket": row["s3_bucket"],
                "region": row["s3_region"],
                "access_key_id": row["s3_access_key_id"],
                "secret_access_key": row["s3_secret_access_key"],
                "endpoint_url": row["s3_endpoint_url"],
                "object_key_prefix": row["s3_object_key_prefix"],
            },
        }
        results.append(cfg)

    return {
        "success": True,
        "message": "Settings retrieved successfully.",
        "result": results[0] if single_result else results,
    }

# ===========================================================================
# Incident Classification Settings
# ===========================================================================
_ALLOWED_INCIDENT_MODELS = {"gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"}


def ensure_system_incident_classification_settings(db: SQLiteDatabase, radio_system_id: int) -> dict:
    """
    Ensure a row exists in radio_system_incident_classification_settings for this system.
    Safe to call repeatedly.

    Returns:
      {success, message, result: incident_classification_setting_id|None}
    """
    rsid = int(radio_system_id)

    ins = db.execute_commit(
        """
        INSERT INTO radio_system_incident_classification_settings (radio_system_id)
        SELECT ? WHERE NOT EXISTS (
            SELECT 1 FROM radio_system_incident_classification_settings WHERE radio_system_id = ?
        )
        """,
        (rsid, rsid),
        return_row_id=False,
    )
    if not ins.get("success"):
        return {"success": False, "message": ins.get("message"), "result": None}

    sel = db.execute_query(
        """
        SELECT incident_classification_setting_id
        FROM radio_system_incident_classification_settings
        WHERE radio_system_id = ?
        """,
        (rsid,),
        fetch_mode="one",
    )
    if not sel.get("success") or not sel.get("result"):
        return {"success": False, "message": sel.get("message", "Settings row missing"), "result": None}

    return {"success": True, "message": "ok", "result": sel["result"]["incident_classification_setting_id"]}


def get_system_incident_classification_settings(
        db: SQLiteDatabase,
        radio_system_id: int | None = None,
        system_decimal: int | None = None,
) -> dict:
    """
    Retrieve incident classification settings for one or all systems.
    """
    base = """
           SELECT
               ics.incident_classification_setting_id,
               ics.radio_system_id,
               ics.incident_classification_enabled,
               ics.openai_api_key,
               ics.openai_model,
               ics.min_confidence,
               rs.system_name
           FROM radio_system_incident_classification_settings AS ics
                    JOIN radio_systems AS rs ON ics.radio_system_id = rs.radio_system_id
           """
    conditions: list[str] = []
    params: list[Any] = []

    if radio_system_id is not None:
        conditions.append("ics.radio_system_id = ?")
        params.append(int(radio_system_id))

    if system_decimal is not None:
        conditions.append("rs.system_decimal = ?")
        params.append(int(system_decimal))

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = base + where + " ORDER BY rs.system_name"

    res = db.execute_query(query, tuple(params), fetch_mode="all" if radio_system_id is None else "one")
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}

    rows = res["result"]
    if not rows:
        return {"success": True, "message": "No settings found.", "result": [] if radio_system_id is None else None}

    single = False
    if not isinstance(rows, list):
        rows = [rows]
        single = True

    out: list[dict] = []
    for row in rows:
        out.append({
            "incident_classification_setting_id": row["incident_classification_setting_id"],
            "radio_system_id": row["radio_system_id"],
            "system_name": row["system_name"],
            "enabled": row["incident_classification_enabled"],
            "openai_api_key": row["openai_api_key"],
            "openai_model": row["openai_model"],
            "min_confidence": row["min_confidence"],
        })

    return {"success": True, "message": "Settings retrieved successfully.", "result": out[0] if single else out}


def update_system_incident_classification_settings(db: SQLiteDatabase, data: dict) -> dict:
    """
    PATCH-style upsert for radio_system_incident_classification_settings.
    Ensures row exists, then updates only provided keys.

    Required:
      - radio_system_id

    Optional:
      - incident_classification_enabled (0/1/bool)
      - openai_api_key (str; may be blank to clear)
      - openai_model (one of _ALLOWED_INCIDENT_MODELS)
      - min_confidence (0..1)
    """
    rsid = data.get("radio_system_id")
    if not rsid:
        return {"success": False, "message": "radio_system_id is required.", "result": []}
    rsid = int(rsid)

    # Ensure row exists (important for existing systems created before this table)
    ensure = ensure_system_incident_classification_settings(db, rsid)
    if not ensure.get("success"):
        return {"success": False, "message": ensure.get("message"), "result": []}

    # Load current row for validation/merge
    cur = db.execute_query(
        "SELECT * FROM radio_system_incident_classification_settings WHERE radio_system_id = ?",
        (rsid,),
        fetch_mode="one",
    )
    if not cur.get("success") or not cur.get("result"):
        return {"success": False, "message": "Failed to load current incident settings.", "result": []}
    current = cur["result"]

    norm: dict[str, Any] = {}

    if "incident_classification_enabled" in data:
        enabled_bool = _bool_like(data.get("incident_classification_enabled"))
        norm["incident_classification_enabled"] = 1 if enabled_bool else 0

    # allow explicit clear for openai_api_key
    if "openai_api_key" in data:
        v = _norm_str(data.get("openai_api_key"))
        norm["openai_api_key"] = None if (v is None or v == "") else v

    if "openai_model" in data:
        m = _norm_str(data.get("openai_model"))
        if m:
            if m not in _ALLOWED_INCIDENT_MODELS:
                return {"success": False, "message": f"Invalid openai_model: {m}", "result": []}
            norm["openai_model"] = m

    if "min_confidence" in data:
        try:
            mc = float(data.get("min_confidence"))
        except (TypeError, ValueError):
            return {"success": False, "message": "min_confidence must be a number (0..1).", "result": []}
        if not (0.0 <= mc <= 1.0):
            return {"success": False, "message": "min_confidence must be between 0.0 and 1.0.", "result": []}
        norm["min_confidence"] = mc

    if not norm:
        return {"success": True, "message": "No changes to apply.", "result": []}

    merged = {**current, **norm}

    # Validation ONLY when enabled
    if int(merged.get("incident_classification_enabled") or 0) == 1:

        if not merged.get("openai_api_key"):
            return {
                "success": False,
                "message": "Cannot enable incident classification without an OpenAI API key.",
                "result": [],
            }

    sets = ", ".join(f"{k} = ?" for k in norm.keys())
    params = list(norm.values()) + [rsid]
    q = f"UPDATE radio_system_incident_classification_settings SET {sets} WHERE radio_system_id = ?"

    upd = db.execute_commit(q, tuple(params), return_count=True)
    if not upd.get("success"):
        return {"success": False, "message": upd.get("message"), "result": []}

    return {"success": True, "message": "Incident classification settings updated.", "result": []}


def delete_system_incident_classification_settings(db: SQLiteDatabase, radio_system_id: int) -> dict:
    """
    Delete the incident classification settings row for a system.
    (Not usually needed because systems delete cascades, but useful for admin/reset.)
    """
    rsid = int(radio_system_id)
    res = db.execute_commit(
        "DELETE FROM radio_system_incident_classification_settings WHERE radio_system_id = ?",
        (rsid,),
        return_count=True,
        return_row_id=False,
    )
    if not res.get("success"):
        return {"success": False, "message": res.get("message"), "result": []}
    if res.get("result", 0) == 0:
        return {"success": False, "message": "Settings row not found.", "result": []}
    return {"success": True, "message": "Incident classification settings deleted.", "result": []}
