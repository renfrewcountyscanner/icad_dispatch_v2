# lib/dispatch_module.py
from __future__ import annotations

import logging
from typing import Optional

from icad_tone_detection import ToneDetectionResult

from lib.discord_module import DiscordSender
from lib.dispatch_text_render import build_context
from lib.make_module import MakeSender
from lib.n8n_module import N8nSender
from lib.pushover_module import PushoverSender
from lib.sqlite_module import SQLiteDatabase
from lib.system_module import get_systems  # you can remove this if you pass config in
from lib.email_module import EmailSender
from lib.telegram_module import TelegramSender

module_logger = logging.getLogger('icad_dispatch.dispatch_module')


def _first_system_row(radio_system_config: dict) -> Optional[dict]:
    try:
        rows = (radio_system_config or {}).get("result") or []
        return rows[0] if rows else None
    except Exception:
        return None


def _dispatch_triggers(
        db: SQLiteDatabase,
        fired_trigger_data: list[dict],
        payload: dict,
        detect_result: ToneDetectionResult,
        *,
        radio_system_config: dict | None = None,
        transcript_text: str | None = None,
        transcript_segments: list[dict] | None = None,
        tz: str,
) -> None:
    """
    Send one consolidated notification (email/discord/telegram/...) for all fired triggers.

    Args:
      db: SQLite handle (not used for email)
      fired_trigger_data: list of trigger rows that fired
      payload: {'radio_system_id', 'talkgroup', 'duration_s', 'start_epoch_s', 'audio_url', ...}
      detect_result: ToneDetectionResult for tones summary
      radio_system_config: (optional) pass-through of system config you already have
      transcript_text / transcript_segments: (optional) if you already have transcription
      tz: REQUIRED IANA timezone name (validated at app startup),
        e.g. "America/New_York".
    """
    module_logger.info(
        "Dispatch Triggers → triggers=%s url=%s",
        [t['alert_trigger_name'] for t in fired_trigger_data],
        payload.get("audio_url"),
    )

    # Use the config you already have; fall back to a lookup if it's not provided.
    if radio_system_config is None:
        radio_system_config = get_systems(
            db,
            radio_system_id=payload["radio_system_id"],
            include_config=True
        )
    module_logger.debug("radio_system_config=%s", radio_system_config)
    module_logger.debug("payload=%s", payload)
    module_logger.debug("fired_trigger_data=%s", fired_trigger_data)

    system_row = _first_system_row(radio_system_config)
    if not system_row:
        module_logger.warning("No system row found; aborting dispatch")
        return

    if system_row.get("mute_notifications"):
        module_logger.info("Notifications muted for system_id=%s, skipping dispatch", system_row.get("radio_system_id"))
        return

    # Build the placeholder context once and reuse for all channels
    # (provides keys like {trigger_list}, {timestamp_24}, {timestamp_12}, {timestamp},
    #  {transcript}, {transcript_segments}, {audio_url}, {stream_url}, {system_name}, etc.)
    ctx = build_context(
        system_row,
        payload,
        fired_trigger_data,
        detect_result=detect_result,
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
        tz=tz,
    )

    # ───────────────────────── Email ─────────────────────────
    emailer = EmailSender.from_system_row(system_row, logger=module_logger)
    if emailer.enabled:
        # Optional overrides if you want to force a custom subject/body per dispatch:
        # subject_override = "{system_name} • {trigger_list} @ {timestamp_24}"
        # body_override = None  # keep system template
        sent = emailer.send(
            ctx,
            # recipients= ["ops@example.com"],   # leave out to use system config recipients
            # subject_override=subject_override,
            # body_html_override=body_override,
        )
        module_logger.info("Email dispatched: %s", sent)
    else:
        module_logger.debug("Email disabled for system_id=%s", system_row.get("radio_system_id"))

    discord = DiscordSender.from_system_row(system_row, logger=module_logger)
    if discord.enabled:
        # ctx is the same dict you pass to email (has {trigger_list}, {audio_url}, {timestamp_24}, etc.)
        ok = discord.send(ctx)
        module_logger.info("Discord dispatched: %s", ok)
    else:
        module_logger.debug("Discord disabled for system_id=%s", system_row.get("radio_system_id"))

    telegram = TelegramSender.from_system_row(system_row, logger=module_logger)
    if telegram.enabled:
        ok = telegram.send(ctx, fired_trigger_data=fired_trigger_data)
        module_logger.info("Telegram dispatched: %s", ok)
    else:
        module_logger.debug("Telegram disabled for system_id=%s", system_row.get("radio_system_id"))

    pushover = PushoverSender.from_system_row(system_row, fired_trigger_data, logger=module_logger)
    if pushover.enabled:
        # 1) Systemwide channel (if enabled on the system)
        sys_ok = pushover.send_system(ctx)
        module_logger.info("Pushover systemwide dispatched: %s", sys_ok)

        # 2) Per-trigger channels (each fired trigger w/ its own tokens)
        trig_count = pushover.send_for_triggers(ctx)
        module_logger.info("Pushover per-trigger dispatched: %d", trig_count)
    else:
        module_logger.debug("Pushover disabled for system_id=%s", system_row.get("radio_system_id"))

    make_sender = MakeSender.from_system_row(system_row, logger=module_logger)
    if make_sender.enabled:
        ok = make_sender.send(ctx)
        module_logger.info("Make webhook dispatched: %s", ok)
    else:
        module_logger.debug("Make webhook disabled for system_id=%s", system_row.get("radio_system_id"))

    n8n = N8nSender.from_system_row(system_row, logger=module_logger)
    if n8n.enabled:
        ok = n8n.send(ctx, fired_trigger_data=fired_trigger_data, only_if_any_trigger_enabled=False)
        module_logger.info("n8n webhook dispatched: %s", ok)
    else:
        module_logger.debug("n8n disabled for system_id=%s", system_row.get("radio_system_id"))


