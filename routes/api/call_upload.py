# routes/api_call_upload.py
"""Upload endpoint – handles audio clips, tone-detection, trigger dispatch
and database persistence (calls / tones / triggers).

This version **implements the new data-model**:
* `call_tone_events.matches_trigger` (bool)
* `tone_trigger_map`     (M-N link tone-set ↔ trigger)
…and guarantees that:
    • every clip with tones is persisted when tone-finder is *on* **or** a
      trigger fires;
    • tone-sets that fired a trigger are flagged + linked;
    • cooling-down / ignore-window observed before dispatch.
"""

from __future__ import annotations

import json, time, threading
import logging
import os
import re
import requests
from dataclasses import is_dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Tuple, Set, Optional, Any
from collections import defaultdict

from flask import Blueprint, request, jsonify, current_app, g, Response
from pydub import AudioSegment
import webrtcvad

from lib.audio_file_handler import (
    validate_audio, AudioValidationError, mute_detected_tones, normalize_audio_loudnorm,
    audiosegment_to_wav_bytes, AudioConversionError,
    reduce_detected_tones_for_transcribe, remap_whisper_timestamps_to_original,
)
from icad_tone_detection import tone_detect, ToneDetectionResult
from lib.dispatch_module import _dispatch_triggers
from lib.file_storage_module import _store_audio_for_system
from lib.incident_classifier_module import IncidentClassificationService
from lib.postgres_module import PostgreSQLDatabase
from lib.transcribe_module import transcribe_audio
from lib.utility import _parse_to_float
from lib.vad_module import vad_segments_webrtc_tone_tx
from routes.decorators import token_required, token_or_login_required
from lib.alert_trigger_module import get_triggers_full

from lib.address_extractor_module import (
    AddressExtractionService,
    AddressExtractorError,
)
from lib.system_module import (
    get_system_address_extraction_settings, update_system_storage_settings, get_system_storage_settings, get_systems, get_system_incident_classification_settings,
)

# -----------------------------------------------------------------------------
#  constants / globals
# -----------------------------------------------------------------------------
_AUDIO_DIR = Path("static/audio")
_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
api_call_upload = Blueprint("api_call_upload", __name__)

# pending tone-only stubs ─────────────────────────────────────────────────────
_pending: Dict[Tuple[int, str], Dict] = {}
_pending_lock = threading.Lock()

_webrtc_vad = webrtcvad.Vad(2)

LOG = {
    "stub":      "📥",
    "merge":     "🔀",
    "tone":      "🔊",
    "trigger":   "🚨",
    "persist":   "💾",
}

def _is_sdrtrunk_test_request() -> bool:
    v = request.values.get("test")
    s = ("" if v is None else str(v)).strip().lower()
    return s in ("1", "true", "t", "yes", "y", "on")

def _tone_counts(td: Dict[str, Any]) -> Dict[str, int]:
    """
    Return counts from call_data["tone_detect"] shape.
    Keys are your canonical payload keys.
    """
    two   = len(td.get("two_tone") or [])
    lng   = len(td.get("long_tone") or [])
    hilo  = len(td.get("hi_low_tone") or [])
    pulse = len(td.get("pulsed_tone") or [])
    dtmf  = len(td.get("dtmf_tone") or [])
    mdc   = len(td.get("mdc_tone") or [])
    total = two + lng + hilo + pulse + dtmf + mdc
    return {
        "total": total,
        "two_tone": two,
        "long": lng,
        "hi_low": hilo,
        "pulsed": pulse,
        "dtmf": dtmf,
        "mdc": mdc,
    }


def _load_system_name(db, radio_system_id: int) -> str:
    """Load system name from database."""
    try:
        res = db.execute_query(
            "SELECT system_name FROM radio_systems WHERE radio_system_id = ?",
            (radio_system_id,),
            fetch_mode="one"
        )
        if res.get("success") and res.get("result"):
            return res["result"].get("system_name", "Unknown")
    except Exception:
        pass
    return "Unknown"


def _count_tone_types(detect_result) -> Dict[str, int]:
    """Count tones by type from ToneDetectionResult."""
    counts = {}
    if detect_result and hasattr(detect_result, 'tones'):
        for tone in detect_result.tones:
            tone_type = tone.tone_type or "unknown"
            counts[tone_type] = counts.get(tone_type, 0) + 1
    return counts


def _dump_json(obj: Any) -> str:
    """
    JSON stringify for logs. Uses default=str to avoid crashing on Decimals, etc.
    """
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _log_tone_detect(route_logger, prefix: str, rsid: int, tg: Any, dur_s: float, td: Dict[str, Any]) -> None:
    """
    INFO: counts only.
    DEBUG: full dict.
    """
    c = _tone_counts(td)

    route_logger.info(
        "%s %s sys=%s tg=%s dur=%.2fs tones_total=%d 2tone=%d long=%d hi/low=%d pulsed=%d mdc=%d dtmf=%d",
        LOG["tone"], prefix,
        rsid, tg, dur_s,
        c["total"], c["two_tone"], c["long"], c["hi_low"], c["pulsed"], c["mdc"], c["dtmf"],
    )

    # Only pay dump cost if DEBUG is enabled
    if route_logger.isEnabledFor(logging.DEBUG):
        route_logger.debug("%s %s tone_detect=%s", LOG["tone"], prefix, _dump_json(td))

@api_call_upload.route("", methods=["POST"])
@token_or_login_required
def call_upload():
    db = current_app.config["db"]
    route_logger = current_app.config["logger"]
    radio_system_id = g.radio_system_id

    # --- SDRTrunk/RdioScanner connection test (multipart: key, system, test=1; no talkgroup, no audio) ---
    if _is_sdrtrunk_test_request() and "audio" not in request.files:
        route_logger.debug(
            "[sdrtrunk-test] sys=%s ct=%s len=%s form=%s files=%s",
            radio_system_id,
            request.content_type,
            request.content_length,
            {k: (v.strip() if isinstance(v, str) else v) for k, v in request.form.items()},
            list(request.files.keys()),
        )

        # MUST start with this exact phrase (client checks .toLowerCase().startsWith(...))
        body = "Incomplete call data: no talkgroup"
        # Status code doesn't matter for SDRTrunk's test logic, but 417 matches "incomplete" semantics.
        return Response(body, status=417, mimetype="text/plain")

    # 1) ---------- input validation --------------------------------------------------
    audio_file = request.files.get("audio")
    if not audio_file:
        return _err("audio field (file) is required", 400)

    upload_cfg = _load_upload_cfg(db, radio_system_id) or {}

    try:
        audio_duration, audio_segment = validate_audio(audio_file, min_duration_s=upload_cfg["audio_min_length"])
    except AudioValidationError as e:
        route_logger.warning("Audio validation failed: %s", e)
        return _err(str(e), 422)

    # 2) ---------- call-data ---------------------------------------------------------
    skip_keys = {"key", "system", "radio_system_id", "audio"}
    call_data = {k: v for k, v in request.values.items() if k not in skip_keys}

    start_epoch = get_start_epoch(call_data)

    call_data.update({
        "radio_system_id": radio_system_id,
        "duration": audio_duration,
        "start_time": start_epoch,
    })

    talkgroup = call_data.get("talkgroup", 0)

    route_logger.info("%s upload sys=%s tg=%s dur=%.2fs raw_len=%d",
                      LOG["tone"], radio_system_id, talkgroup,
                      audio_duration, len(audio_segment))

    # 3) ---------- cfg ----------------------------------------------------------------
    tone_cfg = _load_tone_cfg(db, radio_system_id) or {}
    transcribe_cfg = _load_transcribe_cfg(db, radio_system_id) or {}

    # 4) ---------- tone detection -----------------------------------------------------
    try:
        detect_result = tone_detect(
            audio_segment,
            matching_threshold=tone_cfg["matching_threshold"],
            tone_a_min_length=tone_cfg["tone_a_min_length"],
            tone_b_min_length=tone_cfg["tone_b_min_length"],
            fe_snr_above_noise_db=tone_cfg["fe_snr_above_noise_db"],
            two_tone_max_gap_between_a_b=0.5,  # sec – max A→B gap
            two_tone_bw_hz=25.0,             # Hz – intra-group stability band
            two_tone_min_pair_separation_hz=tone_cfg["two_tone_min_pair_separation_hz"],  # Hz – ensure A and B are distinct
            hi_low_interval=tone_cfg["hi_low_interval"],
            hi_low_min_alternations=tone_cfg["hi_low_min_alternations"],
            hi_low_tone_bw_hz=25.0,          # Hz – stability band
            hi_low_min_pair_separation_hz=25.0,  # Hz – min separation between the two tones
            long_tone_min_length=tone_cfg["long_tone_min_length"],
            long_tone_bw_hz=25.0,
            pulsed_bw_hz=20.0,
            pulsed_min_cycles=tone_cfg["pulsed_min_cycles"],
            pulsed_min_on_ms=tone_cfg["pulsed_min_on_ms"],
            pulsed_max_on_ms=tone_cfg["pulsed_max_on_ms"],
            pulsed_min_off_ms=tone_cfg["pulsed_min_off_ms"],
            pulsed_max_off_ms=tone_cfg["pulsed_max_off_ms"],
            dtmf_min_ms=tone_cfg.get("dtmf_min_ms", 100),
            dtmf_merge_ms=tone_cfg.get("dtmf_merge_ms", 75),
            dtmf_start_offset_ms=tone_cfg.get("dtmf_start_offset_ms",-20),
            dtmf_end_offset_ms=tone_cfg.get("dtmf_end_offset_ms",20),
            dtmf_sequence_gap_s=tone_cfg.get("dtmf_sequence_gap_s", 0.3),
        )
    except Exception as e:  # pragma: no cover – defensive
        route_logger.exception("Tone detection failed: %s", e)
        return _err(f"Tone detection failed: {e}", 500)

    call_data.update({
        "tone_detect": {
            "two_tone": detect_result.two_tone_result,
            "long_tone": detect_result.long_result,
            "hi_low_tone": detect_result.hi_low_result,
            "pulsed_tone": detect_result.pulsed_result,
            "dtmf_tone": detect_result.dtmf_result,
            "mdc_tone": detect_result.mdc_result
        }
    })

    td = call_data.get("tone_detect") or {}
    _log_tone_detect(route_logger, prefix="[detect]", rsid=radio_system_id, tg=talkgroup, dur_s=audio_duration, td=td)

    detect_has_tones = bool(
        detect_result.two_tone_result or detect_result.long_result or detect_result.hi_low_result or detect_result.pulsed_result or detect_result.dtmf_result
    )

    # 5) ---------- split / stub logic --------------------------------------------------
    # a) quick short-circuit: if split is OFF we never cache stubs
    is_stub = False

    if not upload_cfg["split_enabled"]:
        route_logger.debug("[stub-chk] split disabled → VOICE (no stub)")

    else:
        if detect_has_tones:
            # normal “tones present” path
            is_stub = _is_tone_only_stub(
                detection_result   = detect_result,
                audio_segment      = audio_segment,
                system_upload_config = upload_cfg,
                route_logger       = route_logger,
            )

        else:
            if _has_cached_stub(radio_system_id, talkgroup):
                speech_sec, speech_ratio = speech_stats_webrtc(audio_segment)
                route_logger.debug(
                    "[stub-chk] voice-only clip while stub open:"
                    " speech_sec=%.2f tail_min=%.2f",
                    speech_sec, upload_cfg["tail_min_voice_sec"])

                if speech_sec < upload_cfg["tail_min_voice_sec"]:
                    is_stub = True
                    route_logger.debug("[stub-chk] short voice < min → STUB")
                else:
                    route_logger.debug("[stub-chk] voice long enough → VOICE")
            else:
                route_logger.debug("[stub-chk] no tones & no cached stub → VOICE")



    # 5a) ---------- cache stub --------------------------------------------------------
    if is_stub:
        route_logger.info("%s caching stub seg (tg=%s, dur=%.2fs)",
                      LOG["stub"], talkgroup, audio_duration)
        _cache_stub(radio_system_id, talkgroup, audio_segment, audio_duration, call_data, upload_cfg, route_logger)
        return jsonify(success=True, message="Tone-only stub stored; waiting for voice.", result={"radio_system_id": radio_system_id, "stub": True}), 202

    # 5b) ---------- merge with earlier stubs ------------------------------------------
    merged, audio_segment, audio_duration, call_data, detect_result_new = _maybe_merge_stub(
        radio_system_id, talkgroup, audio_segment, audio_duration, call_data, upload_cfg, tone_cfg, route_logger
    )

    route_logger.info(
        "%s merged=%s segs=%s final_dur=%.2fs",
        LOG["merge"], merged,
        ("voice-only" if not merged else
         f"{len(audio_segment)}+stub"),  # optional
        audio_duration)

    # 5c) ---------- Merge tone detect data ------------------------------------------
    if merged and detect_result_new:
        detect_result = detect_result_new
        detect_has_tones = bool(
            detect_result.two_tone_result or detect_result.long_result or detect_result.hi_low_result or detect_result.pulsed_result or detect_result.dtmf_result
        )

    must_persist = bool(tone_cfg["tone_finder_enabled"] and detect_has_tones)
    call_id = None  # Initialize in case not persisting

    # 6) ---------- evaluate triggers ---------------------------------------------------
    fired_trigger_data: List[dict] = []
    fired_trigger_ids: List[int] = []
    matched_tone_ids: Set[str] = set()
    tone_ids_by_trig: Dict[int, Set[str]] = defaultdict(set)  # NEW

    trigger_data = _load_triggers(db, radio_system_id)

    if detect_has_tones:
        now_ts = time.time()
        for trig in trigger_data:
            if _cooling_down(trig, now_ts):
                continue
            # ── talkgroup gate: skip if this trigger targets a different TG ──
            if not _tg_allows_trigger(trig, talkgroup):
                route_logger.debug("[tg] skip trigger %s: call TG=%s ≠ trig TG=%s", trig.get("alert_trigger_id"), talkgroup, trig.get("alert_trigger_talkgroup"))
                continue

            hits = _tones_matching_trigger(trig, detect_result)
            if hits:
                matched_tone_ids.update(hits)
                tone_ids_by_trig[trig["alert_trigger_id"]].update(hits)
                fired_trigger_data.append(trig)
                _set_trigger_fired(db, trig["alert_trigger_id"], now_ts)

    if fired_trigger_data:
        seen_ids: set[int] = set()
        unique_fired: list[dict] = []
        for trig in fired_trigger_data:
            tid = trig.get("alert_trigger_id")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            unique_fired.append(trig)
        fired_trigger_data = unique_fired

    route_logger.debug(
        "%s checked %d triggers → fired_unique=%d matched_tones=%d",
        LOG["trigger"],
        len(trigger_data),
        len(fired_trigger_data),
        len(matched_tone_ids),
    )

    if len(fired_trigger_data) > 0:
        must_persist = True

    route_logger.debug(
        "%s must_persist=%s  tone_finder=%s  detect_has_tones=%s  merged=%s",
        LOG["persist"], must_persist,
        tone_cfg['tone_finder_enabled'], detect_has_tones, merged)

    # Initialize transcribe_response for use outside this block
    transcribe_response = None

    if must_persist:
        transcribe_response = None
        audio_tones_muted = audio_segment
        audio_normalized = audio_segment

        storage_cfg = _load_storage_cfg(db, radio_system_id) or {}

        td = call_data.get("tone_detect") or {}

        route_logger.debug(
            "%s tone-summary: sys=%s tg=%s dur=%.2fs "
            "2tone=%d long=%d hi/low=%d pulsed=%d mdc=%d dtmf=%d "
            "matched=%d fired_triggers=%s",
            LOG["tone"],
            radio_system_id,
            talkgroup,
            audio_duration,
            len(td.get("two_tone") or []),
            len(td.get("long_tone") or []),
            len(td.get("hi_low_tone") or []),
            len(td.get("pulsed_tone") or []),
            len(td.get("mdc_tone") or []),
            len(td.get("dtmf_tone") or []),
            len(matched_tone_ids),
            fired_trigger_ids or [],
            )


        # Generate Filename
        file_name = create_audio_filename(radio_system_id, talkgroup, int(start_epoch))

        if transcribe_cfg.get("enabled", 0):
            try:
                # Remove Tones From Audio Segment
                audio_tones_muted = mute_detected_tones(audio_segment, call_data)

                # Create normalized audio segment
                audio_normalized = normalize_audio_loudnorm(audio_tones_muted)

                # Build a shorter version ONLY for transcription (optional)
                # If you want “pure cut”, use replacement_ms = 0.
                # If you want a tiny spacer, use e.g. replacement_ms = 150.
                replacement_ms = int(transcribe_cfg.get("tone_replacement_ms") or 0)

                audio_for_transcribe, tone_time_map = reduce_detected_tones_for_transcribe(
                    audio_normalized,
                    call_data,
                    replacement_ms=replacement_ms,
                )


                wav_bytes = audiosegment_to_wav_bytes(audio_for_transcribe)

                transcribe_response = transcribe_audio(
                    audio=wav_bytes,
                    filename=file_name.replace(".mp3", ".wav"),
                    transcribe_config=transcribe_cfg,
                    timeout=120.0,
                )

                if isinstance(transcribe_response, dict) and tone_time_map and transcribe_response.get("segments"):
                    transcribe_response = remap_whisper_timestamps_to_original(transcribe_response, tone_time_map)

                if isinstance(transcribe_response, dict):
                    text = transcribe_response.get("text") or ""
                    segs = transcribe_response.get("segments") or []
                    words = transcribe_response.get("words") or []
                    route_logger.info(
                        "Transcription done text_chars=%d segments=%d words=%d",
                        len(text), len(segs), len(words),
                    )
                    route_logger.debug("Transcription response=%s", json.dumps(transcribe_response, ensure_ascii=False))
                else:
                    route_logger.info("Transcription done (non-dict response type=%s)", type(transcribe_response).__name__)
                    route_logger.debug("Transcription response=%r", transcribe_response)

            except AudioConversionError as ace:
                route_logger.error(f"Audio conversion error when transcribing: {ace}")
                transcribe_response = None
            except Exception as e:
                route_logger.error(f"Unexpected error transcribing audio: {e}")
                transcribe_response = None

        # Apply post-tone delay AFTER tone detection (only if tones were detected)
        if detect_has_tones:
            system_res = db.execute_query("SELECT post_tone_delay FROM radio_systems WHERE radio_system_id = ?", (radio_system_id,), fetch_mode="one")
            system_row = system_res.get("result") if system_res.get("success") else None
            post_tone_delay = int(system_row["post_tone_delay"]) if system_row else 0
            if post_tone_delay > 0 and len(audio_segment) > post_tone_delay * 1000:
                delay_ms = post_tone_delay * 1000
                audio_segment = audio_segment[delay_ms:]
                audio_duration = len(audio_segment) / 1000.0
                route_logger.info("Post-tone delay applied: %d seconds trimmed, new duration: %.2fs", post_tone_delay, audio_duration)

        # Save audio based on storage settings (LOCAL / SFTP / S3)
        try:
            file_path_for_db, audio_url = _store_audio_for_system(
                db=db,
                radio_system_id=radio_system_id,
                seg=audio_segment,
                file_name=file_name,
                storage_cfg=storage_cfg,
                audio_archive_path=current_app.config["AUDIO_ARCHIVE_PATH"],
                logger=route_logger,
            )
        except Exception as e:
            route_logger.exception(
                "Failed to store call audio for radio_system_id=%s: %s",
                radio_system_id, e
            )
            return _err(f"Failed to store call audio: {e}", 500)

        # Insert call in to database.
        call_id = _insert_call_record(
            db,
            radio_system_id,
            str(talkgroup),
            file_path_for_db,
            "",
            audio_duration,
            int(start_epoch),
            merged,
            talkgroup_name=call_data.get("talkgroupLabel") or call_data.get("talkgroup_name"),
        )

        # Make call_id available to downstream dispatch templates
        call_data["call_id"] = call_id

        route_logger.info(
            "%s stored call_id=%s radio_system_id=%s file_path=%s url=%s",
            LOG["persist"], call_id, radio_system_id, file_path_for_db, audio_url
        )

        # Create VAD segments so we can put voice segments in database
        # Use Normalized audio for VAD
        try:
            vad_segs = vad_segments_webrtc_tone_tx(
                audio_normalized,
                detect_result=call_data.get("tone_detect", {}),
                transcribe_response=transcribe_response if isinstance(transcribe_response, dict) else None,
                frame_ms=20,
                pad_ms=120,
                tone_guard_ms=80,
                word_guard_ms=120,
                max_word_gap_s=0.60,
                merge_gap_s=0.10,
                min_speech_s=0.25,
                min_silence_s=0.20,
            )
            route_logger.info(" WebRTC VAD vad_segs=%s", vad_segs)
            _insert_vad_segments(db, call_id, vad_segs)
            route_logger.debug("VAD segments: inserted %d segments for call_id=%s", len(vad_segs), call_id)
        except Exception as e:
            route_logger.warning("VAD segment insertion skipped: %s", e)

        if isinstance(transcribe_response, dict) and transcribe_response.get("text"):

            # ───── Address extraction (LLM + optional geocode) ─────
            # Derive township hint from fired trigger names to improve geocoding
            town_hint = _derive_town_hint_from_triggers(fired_trigger_data)
            try:
                addr_payload = _maybe_extract_address_for_call(
                    db=db,
                    radio_system_id=radio_system_id,
                    call_id=call_id,
                    transcript_response=transcribe_response,
                    call_data=call_data,
                    route_logger=route_logger,
                    town_hint=town_hint,
                )
                if addr_payload:
                    route_logger.info(
                        "Address extraction: call_id=%s extracted=%s geocoded=%s",
                        call_id,
                        bool(addr_payload.get("extracted")),
                        bool(addr_payload.get("geocoded")),
                    )
            except Exception as e:
                route_logger.warning("Address extraction skipped: call_id=%s err=%s", call_id, e)

            # ───── Incident classification (LLM only) ─────
            try:
                incident_payload = _maybe_classify_incident_for_call(
                    db=db,
                    radio_system_id=radio_system_id,
                    call_id=call_id,
                    transcript_response=transcribe_response,
                    call_data=call_data,
                    route_logger=route_logger,
                )
                if incident_payload:
                    route_logger.info(
                        "Incident classification: call_id=%s category=%s type=%s",
                        call_id,
                        incident_payload.get("category"),
                        incident_payload.get("incident_type"),
                    )
            except Exception as e:
                route_logger.warning("Incident classification skipped: call_id=%s err=%s", call_id, e)


        # Persist transcript header + segments (if we have a response from the transcriber)
        try:
            if isinstance(transcribe_response, dict) and transcribe_response:
                save_call_transcript(db, call_id, transcribe_response)
                route_logger.debug(
                    "Transcript persisted: call_id=%s segments=%d",
                    call_id, len(transcribe_response.get("segments") or [])
                )
            else:
                route_logger.debug("No transcript payload to persist (transcribe_response missing or not dict).")
        except Exception as e:
            route_logger.warning("Transcript persistence skipped: %s", e)

    # Extract transcript for dispatch and response
    transcript_text = None
    transcript_segments = None
    if isinstance(transcribe_response, dict) and transcribe_response:
        transcript_text = transcribe_response.get("text") or None
        transcript_segments = transcribe_response.get("segments") or None

    if len(fired_trigger_data) > 0:
        route_logger.info("%s dispatching %d triggers on tg=%s",
                      LOG["trigger"], len(fired_trigger_data), talkgroup)

        payload = _make_payload(audio_url, call_data, talkgroup,
                                radio_system_id, audio_duration)

        # dispatch triggers
        _dispatch_triggers(
            db,
            fired_trigger_data,
            payload,
            detect_result,
            transcript_text=transcript_text,
            transcript_segments=transcript_segments,
            tz=current_app.config["TIMEZONE"],
        )

    # 7) ---------- persist tone-sets ---------------------------------------------------
    if must_persist and detect_has_tones:
        _persist_tone_sets(db, call_id, detect_result,
                           matched_tone_ids, fired_trigger_data, tone_ids_by_trig)

    # 8) ---------- trigger_fires rows --------------------------------------------------
    for trig in fired_trigger_data:
        trigger_id = trig["alert_trigger_id"]
        fired_trigger_ids.append(trigger_id)
        _insert_trigger_fire(db, call_id, trigger_id, int(time.time()))

    route_logger.debug("⇢ response merged=%s persisted=%s triggers_fired=%d",
                   merged, must_persist, len(fired_trigger_data))

    # 9) --------- push complete call to public map ------------------------------------
    if must_persist and call_id:
        try:
            _push_call_to_public_map(db, call_id, route_logger)
        except Exception as e:
            route_logger.warning("Push to public_map failed: call_id=%s err=%s", call_id, e)

    # 10) -------- response ------------------------------------------------------------
    tone_count = 0
    tone_types = {}
    if detect_has_tones and detect_result and hasattr(detect_result, 'tones'):
        tone_count = len(detect_result.tones)
        tone_types = _count_tone_types(detect_result)

    return jsonify(
        success=True,
        message="Merged call imported" if merged else "Call imported successfully",
        result={
            "call_id": call_id,
            "radio_system_id": radio_system_id,
            "system_name": _load_system_name(db, radio_system_id),
            "talkgroup": talkgroup,
            "duration_s": audio_duration,
            "merged": merged,
            "tones_detected": tone_count,
            "tone_types": tone_types,
            "triggers_fired": [trig['alert_trigger_name'] for trig in fired_trigger_data],
            "transcript": transcript_text[:200] if transcript_text else None,
            "persisted": must_persist,
        },
    ), 200

# -----------------------------------------------------------------------------
#  Push complete call to public_map (container-to-container, Docker network)
# -----------------------------------------------------------------------------

PUBLIC_MAP_URL = os.environ.get("PUBLIC_MAP_URL", "http://public_map:5000/api/push-call")


def _push_call_to_public_map(
    db: PostgreSQLDatabase,
    call_id: int,
    route_logger,
) -> None:
    """
    Fetch the fully-baked call from the DB and POST it to the public_map.
    Fire-and-forget: logs on failure, never raises.
    """
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
    if not res.get("success"):
        route_logger.warning("Push to public_map: DB query failed for call_id=%s", call_id)
        return
    rows = res.get("result", [])
    if not rows:
        route_logger.warning("Push to public_map: call_id=%s not found in DB", call_id)
        return

    r = rows[0]

    # Build address + lat/lng exactly like public_map does
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

    # Build audio URL
    audio_url = ""
    fp = (r.get("file_path") or "").strip()
    if fp.startswith("http"):
        audio_url = fp
    elif fp:
        audio_url = f"/audio/{fp.replace('static/audio/', '')}"

    # PostgreSQL returns REAL columns as Decimal; cast to native types for JSON serialization
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

    try:
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("PUBLIC_MAP_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = requests.post(
            PUBLIC_MAP_URL,
            json={"calls": [call_payload]},
            timeout=5,
            headers=headers,
        )
        if resp.status_code == 200:
            route_logger.info("Pushed call_id=%s to public_map", call_id)
        else:
            route_logger.warning(
                "Push to public_map failed: call_id=%s status=%s body=%s",
                call_id, resp.status_code, resp.text[:200],
            )
    except requests.exceptions.Timeout:
        route_logger.warning("Push to public_map timed out: call_id=%s", call_id)
    except Exception as e:
        route_logger.warning("Push to public_map error: call_id=%s err=%s", call_id, e)


# -----------------------------------------------------------------------------
#  (helper) build payload dict once
# -----------------------------------------------------------------------------

def get_start_epoch(call_data: dict) -> float:
    """
    Return an epoch-seconds timestamp in UTC.

    Priority:
      1. A numeric `start_time` field (int/float or numeric-string)
      2. An ISO-8601 `dateTime` field (Zulu or +00:00)
      3. time.time()  – “now”

    Accepted ISO examples:
      • 2025-08-02T22:04:38.123456Z
      • 2025-08-02T22:04:38Z
      • 2025-08-02T22:04:38+00:00
    """

    # ── 1) direct epoch from `start_time` ─────────────────────────────
    st = call_data.get("start_time")
    if st is not None:
        try:
            return float(st)           # works for int, float, or numeric string
        except (TypeError, ValueError):
            pass                       # fall through to dateTime

    # ── 2) ISO string from `dateTime` ────────────────────────────────
    iso_ts = call_data.get("dateTime")
    if iso_ts:
        try:
            iso_ts = str(iso_ts).replace("+00:00", "Z")   # normalise
            try:                                          # with microseconds
                dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:                            # without
                dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            pass   # fall through to now()

    # ── 3) default to current time ──────────────────────────────────
    return time.time()

def _make_payload(audio_url: str, cd: Dict, tg: str, sid: int, dur: float) -> Dict:
    payload = {
        "call_id": cd.get("call_id"),
        "radio_system_id": sid,
        "talkgroup": tg,
        "talkgroup_name": cd.get("talkgroupLabel"),
        "duration_s": dur,
        "start_epoch_s": int(cd["start_time"]),
        "audio_url": audio_url,
        "tone_detect": cd.get("tone_detect") or {},
    }

    # Optional enrichment from address extraction
    if cd.get("address_extracted") is not None:
        payload["address_extracted"] = cd["address_extracted"]
    if cd.get("address_geocoded") is not None:
        payload["address_geocoded"] = cd["address_geocoded"]

    # Optional enrichment from incident classification
    if cd.get("incident_category") is not None:
        payload["incident_category"] = cd["incident_category"]
    if cd.get("incident_type") is not None:
        payload["incident_type"] = cd["incident_type"]


    return payload

# -----------------------------------------------------------------------------
#  stub helpers
# -----------------------------------------------------------------------------

def _cache_stub(
        radio_system_id: int,
        talkgroup: str,
        audio_segment: AudioSegment,
        audio_duration: float,
        call_data: Dict,
        system_upload_config: Dict,
        route_logger
):
    """
    Append *seg* to an existing stub or start a new one.

    A stub is discarded and restarted when
        • gap > max_split_interval    OR
        • total length > max_split_length
    """
    key, now = (radio_system_id, talkgroup), time.time()

    with _pending_lock:
        entry = _pending.get(key)

        if entry:
            gap         = now - entry["timestamp_last"]
            if gap > system_upload_config["max_split_interval"]:
                route_logger.debug(
                    "[stub][%s/%s] gap %.1fs > %.1fs → drop %d seg (%.2fs)",
                    radio_system_id, talkgroup,
                    gap, system_upload_config["max_split_interval"],
                    len(entry['segments']), entry['length'])
                entry = None

        if entry is None:
            entry = {
                "segments":        [],          # list[AudioSegment]
                "start_time":      call_data.get("start_time", now),
                "timestamp_last":  now,
                "length":          0.0,
            }

            _pending[key] = entry
            route_logger.debug(
                "[stub][%s/%s] new cache started (start_time=%s)",
                radio_system_id, talkgroup, entry['start_time'])

        # append / merge
        entry["segments"].append(audio_segment)
        entry["length"]        += audio_duration
        entry["timestamp_last"] = now

        route_logger.debug(
            "[stub][%s/%s] +chunk len=%.2fs  → total %.2fs (%d seg)",
            radio_system_id, talkgroup,
            audio_duration, entry["length"], len(entry["segments"]))

def _merge_timeline_parts(stub_segments: list[AudioSegment], voice_seg: AudioSegment, silence_ms: int) -> tuple[list[dict], float]:
    """
    Build a timeline of the merged audio:
      stub0, silence, stub1, silence, ... stubN, silence, voice

    Returns: (parts, voice_start_s)
    """
    parts: list[dict] = []
    t = 0.0
    silence_s = silence_ms / 1000.0

    for i, seg in enumerate(stub_segments):
        d = float(seg.duration_seconds)
        parts.append({"kind": "stub", "idx": i, "start_s": round(t, 3), "end_s": round(t + d, 3), "dur_s": round(d, 3)})
        t += d

        parts.append({"kind": "silence", "idx": i, "start_s": round(t, 3), "end_s": round(t + silence_s, 3), "dur_s": round(silence_s, 3)})
        t += silence_s

    voice_start_s = t
    vd = float(voice_seg.duration_seconds)
    parts.append({"kind": "voice", "idx": 0, "start_s": round(t, 3), "end_s": round(t + vd, 3), "dur_s": round(vd, 3)})

    return parts, voice_start_s

def _maybe_merge_stub(
        radio_system_id: int,
        talkgroup: str,
        audio_segment: AudioSegment,
        audio_duration: float,
        call_data: dict,
        upload_cfg: dict,
        tone_cfg: dict,
        route_logger,
):
    """
    Return (merged?, audio_seg, audio_dur, call_data, detect_result)
    """
    key = (radio_system_id, talkgroup)
    now = time.time()

    with _pending_lock:
        entry = _pending.pop(key, None)

    if not entry:
        route_logger.debug("[merge][%s/%s] no cached stub", *key)
        return False, audio_segment, audio_duration, call_data, None

    age = now - entry["timestamp_last"]
    if age > upload_cfg["max_split_interval"]:
        route_logger.debug(
            "[merge][%s/%s] last stub %.1fs old > %.1fs → discard",
            *key, age, upload_cfg["max_split_interval"])
        return False, audio_segment, audio_duration, call_data, None

    # ── merge ─────────────────────────────────────────────────────────
    route_logger.debug(
        "[merge][%s/%s] merging %d stub seg (%.2fs) + voice %.2fs",
        *key, len(entry["segments"]), entry["length"], audio_duration)

    silence   = AudioSegment.silent(duration=1500)
    merged_seg = entry["segments"][0]
    for s in entry["segments"][1:]:
        merged_seg += silence + s
    merged_seg += silence + audio_segment
    merged_dur  = merged_seg.duration_seconds

    # call_data
    merged_cd = {**call_data,
                 "start_time": entry["start_time"],
                 "duration"  : merged_dur}

    det = tone_detect(
        merged_seg,
        matching_threshold=tone_cfg["matching_threshold"],
        tone_a_min_length=tone_cfg["tone_a_min_length"],
        tone_b_min_length=tone_cfg["tone_b_min_length"],
        fe_snr_above_noise_db=tone_cfg["fe_snr_above_noise_db"],
        two_tone_max_gap_between_a_b=0.5,  # sec – max A→B gap
        two_tone_bw_hz=25.0,             # Hz – intra-group stability band
        two_tone_min_pair_separation_hz=tone_cfg["two_tone_min_pair_separation_hz"],  # Hz – ensure A and B are distinct
        hi_low_interval=tone_cfg["hi_low_interval"],
        hi_low_min_alternations=tone_cfg["hi_low_min_alternations"],
        hi_low_tone_bw_hz=25.0,          # Hz – stability band
        hi_low_min_pair_separation_hz=25.0,  # Hz – min separation between the two tones
        long_tone_min_length=tone_cfg["long_tone_min_length"],
        long_tone_bw_hz=25.0,
        pulsed_bw_hz=20.0,
        pulsed_min_cycles=tone_cfg["pulsed_min_cycles"],
        pulsed_min_on_ms=tone_cfg["pulsed_min_on_ms"],
        pulsed_max_on_ms=tone_cfg["pulsed_max_on_ms"],
        pulsed_min_off_ms=tone_cfg["pulsed_min_off_ms"],
        pulsed_max_off_ms=tone_cfg["pulsed_max_off_ms"],
        dtmf_min_ms=tone_cfg.get("dtmf_min_ms", 100),
        dtmf_merge_ms=tone_cfg.get("dtmf_merge_ms", 75),
        dtmf_start_offset_ms=tone_cfg.get("dtmf_start_offset_ms",-20),
        dtmf_end_offset_ms=tone_cfg.get("dtmf_end_offset_ms",20),
        dtmf_sequence_gap_s=tone_cfg.get("dtmf_sequence_gap_s", 0.3),
    )
    merged_cd["tone_detect"] = {
        "two_tone"  : det.two_tone_result,
        "long_tone" : det.long_result,
        "hi_low_tone": det.hi_low_result,
        "pulsed_tone": det.pulsed_result,
        "dtmf_tone" : det.dtmf_result,
        "mdc_tone"  : det.mdc_result,
    }

    td = merged_cd.get("tone_detect") or {}
    _log_tone_detect(route_logger, prefix="[merge-detect]", rsid=radio_system_id, tg=talkgroup, dur_s=merged_dur, td=td)

    if route_logger.isEnabledFor(logging.DEBUG):
        silence_ms = 1500

        stub_n = len(entry["segments"])
        stub_dur_sum = sum(float(s.duration_seconds) for s in entry["segments"])
        voice_dur = float(audio_segment.duration_seconds)

        # Your merge code inserts ONE 1500ms silence after EACH stub segment,
        # including after the last stub right before voice.
        silence_count = stub_n
        silence_total = (silence_ms / 1000.0) * silence_count

        expected = stub_dur_sum + silence_total + voice_dur
        drift = float(merged_dur) - float(expected)

        parts, voice_start_s = _merge_timeline_parts(entry["segments"], audio_segment, silence_ms)

        route_logger.debug(
            "[merge][%s/%s] done stub_segs=%d stub_dur=%.3fs voice_dur=%.3fs "
            "silence_ms=%d silence_count=%d silence_total=%.3fs "
            "merged_dur=%.3fs expected=%.3fs drift=%.3fs "
            "age=%.2fs start_epoch=%s voice_start=%.3fs",
            *key,
            stub_n, stub_dur_sum, voice_dur,
            silence_ms, silence_count, silence_total,
            float(merged_dur), float(expected), drift,
            float(age),
            entry.get("start_time"),
            float(voice_start_s),
        )

        # Heavy but extremely useful when debugging “where did my voice land?”
        route_logger.debug("[merge][%s/%s] timeline=%s", *key, _dump_json(parts))

    return True, merged_seg, merged_dur, merged_cd, det

def _has_cached_stub(rsid: int, tg: str) -> bool:
    """True if we currently have an open stub for (rsid, talkgroup)."""
    with _pending_lock:
        return (rsid, tg) in _pending

# -----------------------------------------------------------------------------
#  persistence helpers – tones + mappings
# -----------------------------------------------------------------------------

def _persist_tone_sets(db, call_id: int, det: ToneDetectionResult,
                       match_ids: Set[str], fired_trig_data: List[dict], tone_ids_by_trig: Dict[int, Set[str]]):
    tone_id_to_evt: Dict[str, int] = {}

    # two-tone
    for g in det.two_tone_result:
        eid = _save_tone_event(db, call_id, g, "two_tone", g.get("tone_id") in match_ids)
        tone_id_to_evt[g["tone_id"]] = eid

    # long
    for g in det.long_result:
        eid = _save_tone_event(db, call_id, g, "long", g.get("tone_id") in match_ids)
        tone_id_to_evt[g["tone_id"]] = eid

    # hi/low
    for g in det.hi_low_result:
        eid = _save_tone_event(db, call_id, g, "hi_low", g.get("tone_id") in match_ids)
        tone_id_to_evt[g["tone_id"]] = eid

    # pulsed
    for g in getattr(det, "pulsed_result", []):
        eid = _save_tone_event(db, call_id, g, "pulsed", g.get("tone_id") in match_ids)
        tone_id_to_evt[g["tone_id"]] = eid

    # MDC & DTMF (if implemented)
    for g in det.mdc_result:
        eid = _save_tone_event(db, call_id, g, "mdc", g.get("tone_id") in match_ids)
        tone_id_to_evt[g["tone_id"]] = eid

    for g in det.dtmf_result:
        eid = _save_tone_event(db, call_id, g, "dtmf", g.get("tone_id") in match_ids)
        tone_id_to_evt[g["tone_id"]] = eid

    # link ↔ triggers (only link tone_ids we actually saved)
    if fired_trig_data and tone_ids_by_trig:
        for trig in fired_trig_data:
            trig_id = trig["alert_trigger_id"]
            for tid in tone_ids_by_trig.get(trig_id, set()):
                evt_id = tone_id_to_evt.get(tid)
                if evt_id:
                    _insert_tone_trigger_map(db, evt_id, trig_id)
# -----------------------------------------------------------------------------
#  tone-⇄-trigger matching helpers
# -----------------------------------------------------------------------------

def _tg_allows_trigger(trig: Dict, call_tg: str | int | None) -> bool:
    """
    Return True if the trigger has no TG restriction or the call's TG matches.
    Trigger stores an integer (decimal). Calls may pass TG as str/int/None.
    """
    req = trig.get("alert_trigger_talkgroup")
    if req in (None, ""):
        return True  # unrestricted trigger

    # coerce both sides to int; if call has no/invalid TG, it's not a match
    try:
        req_i = int(req)
    except (TypeError, ValueError):
        return True   # defensive: if DB has a bad value, treat as unrestricted

    try:
        call_i = int(str(call_tg))  # call_tg may be str
    except (TypeError, ValueError):
        return False

    return call_i == req_i

def _tones_matching_trigger(trig: Dict, det) -> Set[str]:
    """
    Return a set of matched tone_ids for this trigger.

    Semantics:
      - AND: every configured rule (across ALL rule tables) must match at least one detection
      - OR:  any one configured rule matching is enough

    If the trigger has zero valid rules, returns empty set.
    """

    def pct_ok(detected: float | None, target: float | None, tol_pct: float) -> bool:
        if detected is None or target in (None, 0, 0.0):
            return False
        try:
            d = float(detected); t = float(target)
            return abs(d - t) / (t if t else 1.0) * 100.0 <= float(tol_pct)
        except (TypeError, ValueError, ZeroDivisionError):
            return False

    def coerce_float(x, default=None):
        try:
            return float(x) if x not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def coerce_int(x, default=None):
        try:
            return int(x) if x not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def _compile_like(pattern: str) -> re.Pattern:
        """
        SQL-LIKE matcher where % = .* (any chars, incl empty).
        Anchored to full string, so it behaves like LIKE does.
        """
        # build regex safely (escape everything except %)
        rx = "^" + "".join(".*" if ch == "%" else re.escape(ch) for ch in pattern) + "$"
        return re.compile(rx)

    trig_type = (trig.get("alert_trigger_type") or "AND").strip().upper()
    trig_type = "OR" if trig_type == "OR" else "AND"

    trig_tol_default = coerce_float(trig.get("alert_trigger_tone_tolerance"), 2.0)

    # Each entry is "matches for one rule row"
    per_rule_hits: list[Set[str]] = []

    # ─────────────────────────────────────────────────────────────
    # Two-tone rules (each row must match independently)
    # ─────────────────────────────────────────────────────────────
    for rule in (trig.get("two_tone_sets") or []):
        ra = coerce_float(rule.get("freq_a_hz"))
        rb = coerce_float(rule.get("freq_b_hz"))
        la = coerce_float(rule.get("min_len_a_s"), 0.0)
        lb = coerce_float(rule.get("min_len_b_s"), 0.0)
        tol = coerce_float(rule.get("tol_pct"), trig_tol_default)

        # invalid rule -> ignore (doesn't participate in AND/OR)
        if ra in (None, 0.0) or rb in (None, 0.0):
            continue

        hits: Set[str] = set()
        for g in getattr(det, "two_tone_result", []) or []:
            det_list = g.get("detected") or []
            if len(det_list) < 2:
                continue
            a_ok = pct_ok(det_list[0], ra, tol) and (coerce_float(g.get("tone_a_length"), 0.0) >= la)
            b_ok = pct_ok(det_list[1], rb, tol) and (coerce_float(g.get("tone_b_length"), 0.0) >= lb)
            if a_ok and b_ok and g.get("tone_id"):
                hits.add(g["tone_id"])

        per_rule_hits.append(hits)

    # ─────────────────────────────────────────────────────────────
    # Long tone rules
    # ─────────────────────────────────────────────────────────────
    for rule in (trig.get("long_tone_sets") or []):
        f   = coerce_float(rule.get("freq_hz"))
        ln  = coerce_float(rule.get("min_len_s"), 0.0)
        tol = coerce_float(rule.get("tol_pct"), trig_tol_default)

        if f in (None, 0.0):
            continue

        hits: Set[str] = set()
        for g in getattr(det, "long_result", []) or []:
            if pct_ok(g.get("detected"), f, tol) and coerce_float(g.get("length"), 0.0) >= ln and g.get("tone_id"):
                hits.add(g["tone_id"])

        per_rule_hits.append(hits)

    # ─────────────────────────────────────────────────────────────
    # Hi/Low rules
    # ─────────────────────────────────────────────────────────────
    for rule in (trig.get("hi_low_sets") or []):
        ha      = coerce_float(rule.get("hi_freq_a_hz"))
        hb      = coerce_float(rule.get("hi_freq_b_hz"))
        alt_min = coerce_int(rule.get("min_alternations"), 4) or 4
        tol     = coerce_float(rule.get("tol_pct"), trig_tol_default)

        if ha in (None, 0.0) or hb in (None, 0.0):
            continue

        hits: Set[str] = set()
        for g in getattr(det, "hi_low_result", []) or []:
            dl = g.get("detected") or []
            if len(dl) < 2:
                continue
            low, high = dl[0], dl[1]
            pair_ok = (pct_ok(low, ha, tol) and pct_ok(high, hb, tol)) or \
                      (pct_ok(low, hb, tol) and pct_ok(high, ha, tol))
            if pair_ok and int(g.get("alternations") or 0) >= alt_min and g.get("tone_id"):
                hits.add(g["tone_id"])

        per_rule_hits.append(hits)

    # ─────────────────────────────────────────────────────────────
    # Pulsed rules
    # ─────────────────────────────────────────────────────────────
    for rule in (trig.get("pulsed_sets") or []):
        center  = coerce_float(rule.get("center_hz"))
        min_cyc = coerce_int(rule.get("min_cycles"), 6) or 6
        tol     = coerce_float(rule.get("tol_pct"), trig_tol_default)

        if center in (None, 0.0):
            continue

        hits: Set[str] = set()
        for g in getattr(det, "pulsed_result", []) or []:
            if pct_ok(g.get("detected"), center, tol) and int(g.get("cycles") or 0) >= min_cyc and g.get("tone_id"):
                hits.add(g["tone_id"])

        per_rule_hits.append(hits)

    # ─────────────────────────────────────────────────────────────
    # DTMF rules
    # ─────────────────────────────────────────────────────────────
    for rule in (trig.get("dtmf_sequences") or []):
        pattern = (str(rule.get("sequence") or "")).upper().strip()
        if not pattern:
            continue

        want_re = _compile_like(pattern)

        hits: Set[str] = set()
        for g in getattr(det, "dtmf_result", []) or []:
            got = (str(g.get("digit") or "")).upper().strip()
            if not got:
                continue

            if want_re.match(got) and g.get("tone_id"):
                hits.add(g["tone_id"])

        per_rule_hits.append(hits)

    # No (valid) rules => never fire
    if not per_rule_hits:
        return set()

    # OR: any rule hit => return union of all hits (or empty if none hit)
    if trig_type == "OR":
        out: Set[str] = set()
        for s in per_rule_hits:
            out.update(s)
        return out

    # AND: every rule must have >=1 hit
    if any(not s for s in per_rule_hits):
        return set()

    out: Set[str] = set()
    for s in per_rule_hits:
        out.update(s)
    return out

def _tones_matching_trigger_old(trig: Dict, det: ToneDetectionResult) -> Set[str]:
    """
    Return set of tone_ids matched by *trig* against detection results.

    If any child arrays are present on the trigger, evaluate those.
    Otherwise, fall back to legacy flat columns.
    """
    def pct_ok(detected: float | None, target: float | None, tol_pct: float) -> bool:
        if detected is None or target in (None, 0, 0.0):
            return False
        try:
            d = float(detected); t = float(target)
            return abs(d - t) / (t if t else 1.0) * 100.0 <= float(tol_pct)
        except (TypeError, ValueError, ZeroDivisionError):
            return False

    def coerce_float(x, default=None):
        try:
            return float(x) if x not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def coerce_int(x, default=None):
        try:
            return int(x) if x not in (None, "") else default
        except (TypeError, ValueError):
            return default

    hits: Set[str] = set()
    trig_tol_default = coerce_float(trig.get("alert_trigger_tone_tolerance"), 2.0)

    has_child_rules = any(bool(trig.get(k)) for k in (
        "two_tone_sets", "long_tone_sets", "hi_low_sets", "pulsed_sets", "dtmf_sequences"
    ))

    # =========================================================================
    # CHILD RULE-SETS (new model)
    # =========================================================================
    if has_child_rules:
        # ---------- Two-tone sets ----------
        for rule in (trig.get("two_tone_sets") or []):
            ra = coerce_float(rule.get("freq_a_hz"))
            rb = coerce_float(rule.get("freq_b_hz"))
            la = coerce_float(rule.get("min_len_a_s"), 0.0)
            lb = coerce_float(rule.get("min_len_b_s"), 0.0)
            tol = coerce_float(rule.get("tol_pct"), trig_tol_default)

            if ra in (None, 0.0) or rb in (None, 0.0):
                continue

            for g in det.two_tone_result:
                det_list = g.get("detected") or []
                if len(det_list) < 2:
                    continue
                a_ok = pct_ok(det_list[0], ra, tol) and (coerce_float(g.get("tone_a_length"), 0.0) >= la)
                b_ok = pct_ok(det_list[1], rb, tol) and (coerce_float(g.get("tone_b_length"), 0.0) >= lb)
                if a_ok and b_ok and g.get("tone_id"):
                    hits.add(g["tone_id"])

        # ---------- Long tone sets ----------
        for rule in (trig.get("long_tone_sets") or []):
            f = coerce_float(rule.get("freq_hz"))
            ln = coerce_float(rule.get("min_len_s"), 0.0)
            tol = coerce_float(rule.get("tol_pct"), trig_tol_default)
            if f in (None, 0.0):
                continue
            for g in det.long_result:
                if pct_ok(g.get("detected"), f, tol) and coerce_float(g.get("length"), 0.0) >= ln and g.get("tone_id"):
                    hits.add(g["tone_id"])

        # ---------- Hi/Low sets ----------
        for rule in (trig.get("hi_low_sets") or []):
            # The detector stores detected as [low, high]
            ha = coerce_float(rule.get("hi_freq_a_hz"))
            hb = coerce_float(rule.get("hi_freq_b_hz"))
            alt_min = coerce_int(rule.get("min_alternations"), 4) or 4
            tol = coerce_float(rule.get("tol_pct"), trig_tol_default)
            if ha in (None, 0.0) or hb in (None, 0.0):
                continue

            for g in det.hi_low_result:
                dl = g.get("detected") or []
                if len(dl) < 2:
                    continue
                low, high = dl[0], dl[1]
                # Try both pairings in case rule/detector naming differs
                pair_ok = (pct_ok(low, ha, tol) and pct_ok(high, hb, tol)) or \
                          (pct_ok(low, hb, tol) and pct_ok(high, ha, tol))
                if pair_ok and int(g.get("alternations") or 0) >= alt_min and g.get("tone_id"):
                    hits.add(g["tone_id"])

        # ---------- Pulsed sets ----------
        for rule in (trig.get("pulsed_sets") or []):
            center   = coerce_float(rule.get("center_hz"))
            min_cyc  = coerce_int(rule.get("min_cycles"), 6) or 6
            tol      = coerce_float(rule.get("tol_pct"), trig_tol_default)
            if center in (None, 0.0):
                continue
            for g in getattr(det, "pulsed_result", []):
                if pct_ok(g.get("detected"), center, tol) and int(g.get("cycles") or 0) >= min_cyc and g.get("tone_id"):
                    hits.add(g["tone_id"])

        # ---------- DTMF sequences ----------
        for rule in (trig.get("dtmf_sequences") or []):
            want = (str(rule.get("sequence") or "")).upper()
            if not want:
                continue
            mtype = (rule.get("match_type") or "EXACT").upper()
            # max_gap_ms is available if you later add multi-press timing logic
            for g in det.dtmf_result:
                got = (str(g.get("sequence") or "")).upper()
                if not got:
                    continue
                ok = (
                        (mtype == "EXACT" and got == want) or
                        (mtype == "PREFIX" and got.startswith(want)) or
                        (mtype == "CONTAINS" and want in got)
                )
                if ok and g.get("tone_id"):
                    hits.add(g["tone_id"])

        return hits

    return hits

# -----------------------------------------------------------------------------
#  cooling-down helper
# -----------------------------------------------------------------------------

def _cooling_down(trig: Dict, now_ts: float) -> bool:
    ignore_for = float(trig["alert_trigger_ignore_time"] or 300)
    last = trig.get("last_fired_at", 0)
    return bool(last and (now_ts - last) < ignore_for)

# -----------------------------------------------------------------------------
#  DB I/O helpers that *changed*  (save tone event & mapping)
# -----------------------------------------------------------------------------

def _save_tone_event(db, call_id: int, g: dict, tone_type: str, matched: bool) -> int:
    det = g.get("detected")

    if tone_type == "two_tone":
        fa = _parse_to_float(det[0] if isinstance(det, (list, tuple)) and len(det) > 0 else None)
        fb = _parse_to_float(det[1] if isinstance(det, (list, tuple)) and len(det) > 1 else None)
        la = _parse_to_float(g.get("tone_a_length"))
        lb = _parse_to_float(g.get("tone_b_length"))

    elif tone_type == "hi_low":
        # detected is [low, high]
        fa = _parse_to_float(det[0] if isinstance(det, (list, tuple)) and len(det) > 0 else None)
        fb = _parse_to_float(det[1] if isinstance(det, (list, tuple)) and len(det) > 1 else None)
        la = _parse_to_float(g.get("length"))   # store total in A; keep full detail in JSON
        lb = None

    elif tone_type == "pulsed":
        # detected: center Hz; keep cycles/on/off in JSON; store any total length seconds if provided
        fa = _parse_to_float(det)
        fb = None
        # prefer a true seconds length if detector provides it; otherwise None (cycles live in JSON)
        la = _parse_to_float(g.get("length"))
        lb = None

    else:
        # long, mdc, dtmf → single value in fa; seconds (if any) in la
        fa = _parse_to_float(det)
        fb = None
        la = _parse_to_float(g.get("length"))
        lb = None

    s = _parse_to_float(g.get("start"))
    e = _parse_to_float(g.get("end"))

    cols = (
        call_id,
        tone_type,
        g["tone_id"],
        json.dumps(g),
        fa, fb,
        la, lb,
        s, e,
        int(matched),
    )

    ins = db.execute_commit(
        """
        INSERT INTO call_tone_events(
            call_id, tone_type, tone_set_id,
            json_payload, freq_a, freq_b,
            length_a_s, length_b_s, start_s, end_s,
            matches_trigger)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        cols,
        return_row_id=True,
    )
    if not ins["success"]:
        raise RuntimeError(ins["message"])
    return ins["result"]


def _insert_tone_trigger_map(db, tone_event_id: int, trig_id: int):
    db.execute_commit(
        "INSERT INTO tone_trigger_map(tone_event_id,alert_trigger_id) VALUES (?,?) ON CONFLICT DO NOTHING",
        (tone_event_id, trig_id),
    )

# ────────────────────────────────────────────────────────────────────────────
#  helper functions
# ────────────────────────────────────────────────────────────────────────────
def _err(msg, code=400):
    return jsonify(success=False, message=msg, result=[]), code

def _load_upload_cfg(db, rsid: int) -> dict:
    row = db.execute_query(
        """
        SELECT split_enabled,
               tail_min_voice_sec,
               vad_min_speech_ratio,
               voice_rms_dbfs,
               max_split_interval,
               max_split_length,
               audio_min_length
        FROM   radio_system_upload_settings
        WHERE  radio_system_id = ?
        """, (rsid,), fetch_mode="one")["result"] or {}
    # SQLite returns Decimal for REAL columns through pysqlite – cast to float
    return {
        "split_enabled"        : bool(row.get("split_enabled", 0)),
        "audio_min_length"     : float(row.get("audio_min_length", 2.0)),
        "tail_min_voice_sec"   : float(row.get("tail_min_voice_sec",   3.0)),
        "vad_min_speech_ratio" : float(row.get("vad_min_speech_ratio", 0.15)),
        "voice_rms_dbfs"       : float(row.get("voice_rms_dbfs",     -35.0)),
        "max_split_interval"   : float(row.get("max_split_interval",  30.0)),
        "max_split_length"     : float(row.get("max_split_length",    30.0)),
    }

def _load_tone_cfg(db, rsid: int) -> dict:
    row = db.execute_query(
        """
        SELECT tone_finder_enabled,
               matching_threshold,
               fe_snr_above_noise_db,
               tone_a_min_length,
               tone_b_min_length,
               two_tone_min_pair_separation_hz,
               hi_low_interval,
               hi_low_min_alternations,
               long_tone_min_length,
               -- 🆕 pulsed knobs
               pulsed_min_cycles,
               pulsed_min_on_ms,
               pulsed_max_on_ms,
               pulsed_min_off_ms,
               pulsed_max_off_ms,
               -- DTMF knobs
               dtmf_min_ms,
               dtmf_merge_ms,
               dtmf_start_offset_ms,
               dtmf_end_offset_ms,
               dtmf_sequence_gap_s
        FROM   radio_system_tone_settings
        WHERE  radio_system_id = ?
        """, (rsid,), fetch_mode="one"
    )["result"] or {}

    return {
        "tone_finder_enabled"     : bool(row.get("tone_finder_enabled", 0)),
        "matching_threshold"      : float(row.get("matching_threshold",       2.0)),
        "fe_snr_above_noise_db"   : float(row.get("fe_snr_above_noise_db", 1.0)),
        "tone_a_min_length"       : float(row.get("tone_a_min_length",        0.7)),
        "tone_b_min_length"       : float(row.get("tone_b_min_length",        2.6)),
        "two_tone_min_pair_separation_hz" : float(row.get("two_tone_min_pair_separation_hz", 10.0)),
        "hi_low_interval"         : float(row.get("hi_low_interval",          0.2)),
        "hi_low_min_alternations" : int  (row.get("hi_low_min_alternations",  6)),
        "long_tone_min_length"    : float(row.get("long_tone_min_length",     1.8)),
        "pulsed_min_cycles"       : int  (row.get("pulsed_min_cycles",        6)),
        "pulsed_min_on_ms"        : int  (row.get("pulsed_min_on_ms",       120)),
        "pulsed_max_on_ms"        : int  (row.get("pulsed_max_on_ms",       900)),
        "pulsed_min_off_ms"       : int  (row.get("pulsed_min_off_ms",       25)),
        "pulsed_max_off_ms"       : int  (row.get("pulsed_max_off_ms",      350)),
        "dtmf_min_ms"             : float(row.get("dtmf_min_ms", 100.0)),
        "dtmf_merge_ms"           : float(row.get("dtmf_merge_ms", 75)),
        "dtmf_start_offset_ms"    : float(row.get("dtmf_start_offset_ms", -20)),
        "dtmf_end_offset_ms"      : float(row.get("dtmf_end_offset_ms", 20)),
        "dtmf_sequence_gap_s"     : float(row.get("dtmf_sequence_gap_s", 0.3)),
    }

def _load_transcribe_cfg(db, rsid: int) -> dict:
    """
    Return a get_system-style block:
    {
      "transcribe_setting_id": int|None,
      "enabled": bool|None,
      "url": str|None,
      "api_key": str|None,
      "model": str|None,
      "language": str|None,
      "prompt": str|None,
    }
    """
    def _b(x):
        if x is None: return None
        if isinstance(x, bool): return x
        s = str(x).strip().lower()
        if s in ("1","true","t","yes","y","on"): return True
        if s in ("0","false","f","no","n","off"): return False
        try:
            return bool(int(x))
        except Exception:
            return None

    try:
        r = db.execute_query(
            """
            SELECT
                transcribe_setting_id,
                transcribe_enabled,
                transcribe_url,
                transcribe_api_key,
                transcribe_model,
                transcribe_language,
                transcribe_prompt
            FROM radio_system_transcribe_settings
            WHERE radio_system_id = ?
            """,
            (rsid,), fetch_mode="one"
        )
        if r.get("success") and r.get("result"):
            row = r["result"]
            return {
                "transcribe_setting_id": row.get("transcribe_setting_id"),
                "enabled": _b(row.get("transcribe_enabled")),
                "url": row.get("transcribe_url"),
                "api_key": row.get("transcribe_api_key"),
                "model": row.get("transcribe_model"),
                "language": row.get("transcribe_language"),
                "prompt": row.get("transcribe_prompt"),
            }
    except Exception:
        pass  # table might not exist; that's fine

    # No row → return empty; helper will supply hardcoded defaults later.
    return {}

def _load_storage_cfg(db, radio_system_id: int) -> dict:
    """
    Load storage settings for a system (LOCAL / SFTP / S3) with sane defaults.
    Returns a dict shaped like the JSON you send to the UI:
      {
        "radio_system_id": ...,
        "system_name": ...,
        "storage_type": "LOCAL" | "SFTP" | "S3",
        "path_pattern": "%Y/%m/%d",
        "sftp": {...},
        "s3": {...},
      }
    """
    logger = current_app.logger

    res = get_system_storage_settings(db, radio_system_id=radio_system_id)
    if not res.get("success"):
        logger.warning("Storage cfg: get failed for radio_system_id=%s: %s",
                       radio_system_id, res.get("message"))
        return {
            "radio_system_id": radio_system_id,
            "storage_type": "LOCAL",
            "path_pattern": "%Y/%m",
            "sftp": {},
            "s3": {},
        }

    cfg = res.get("result") or {}

    # Ensure a row exists (mirrors _fetch_storage_settings_obj logic)
    if not cfg:
        ensure = update_system_storage_settings(db, {"radio_system_id": radio_system_id})
        if not ensure.get("success"):
            logger.warning("Storage cfg: ensure failed for radio_system_id=%s: %s",
                           radio_system_id, ensure.get("message"))
            return {
                "radio_system_id": radio_system_id,
                "storage_type": "LOCAL",
                "path_pattern": "%Y/%m",
                "sftp": {},
                "s3": {},
            }
        res = get_system_storage_settings(db, radio_system_id=radio_system_id)
        cfg = res.get("result") or {}

    cfg.setdefault("radio_system_id", radio_system_id)
    cfg.setdefault("storage_type", "LOCAL")
    cfg.setdefault("path_pattern", "%Y/%m")
    cfg.setdefault("sftp", {})
    cfg.setdefault("s3", {})

    return cfg

# ----------------------------------------------------------------------
#  TRIGGER HELPERS
# ----------------------------------------------------------------------
def _load_triggers(db, rsid: int) -> list[dict]:
    """
    Return enabled triggers for the system, including:
      • child rule-sets (via get_triggers_full)
      • pushover settings columns
      • last_fired_at
    """
    # Load full triggers (with child arrays)
    base = get_triggers_full(db, radio_system_id=rsid)
    if not base.get("success"):
        return []

    rows = [r for r in (base.get("result") or []) if r.get("alert_trigger_enabled")]

    if not rows:
        return []

    trig_ids = [r["alert_trigger_id"] for r in rows]

    # Pushover extras
    if trig_ids:
        placeholders = ",".join("?" for _ in trig_ids)
        po = db.execute_query(
            f"""
            SELECT alert_trigger_id,
                   enable_pushover,
                   pushover_group_token,
                   pushover_app_token,
                   pushover_body,
                   pushover_subject,
                   pushover_sound
            FROM alert_trigger_pushover_settings
            WHERE alert_trigger_id IN ({placeholders})
            """,
            trig_ids,
            fetch_mode="all",
        )
        po_map = {r["alert_trigger_id"]: r for r in (po.get("result") or [])} if po.get("success") else {}

        lf = db.execute_query(
            f"""
            SELECT alert_trigger_id, last_fired_at
            FROM alert_trigger_last_fire
            WHERE alert_trigger_id IN ({placeholders})
            """,
            trig_ids,
            fetch_mode="all",
        )
        lf_map = {r["alert_trigger_id"]: r.get("last_fired_at") for r in (lf.get("result") or [])} if lf.get("success") else {}

        for r in rows:
            po_row = po_map.get(r["alert_trigger_id"], {})
            r.update({
                "enable_pushover":     po_row.get("enable_pushover"),
                "pushover_group_token":po_row.get("pushover_group_token"),
                "pushover_app_token":  po_row.get("pushover_app_token"),
                "pushover_body":       po_row.get("pushover_body"),
                "pushover_subject":    po_row.get("pushover_subject"),
                "pushover_sound":      po_row.get("pushover_sound"),
                "last_fired_at":       lf_map.get(r["alert_trigger_id"]),
            })

    return rows

def _set_trigger_fired(db, trig_id: int, ts: float) -> None:
    """
    Upsert last_fired_at for the trigger, only moving forward in time.
    Assumes:
      - SQLite
      - alert_trigger_last_fire.alert_trigger_id is PRIMARY KEY (or UNIQUE)
      - last_fired_at stored as integer epoch seconds
    """
    db.execute_commit(
        """
        INSERT INTO alert_trigger_last_fire (alert_trigger_id, last_fired_at)
        VALUES (?, ?)
        ON CONFLICT(alert_trigger_id) DO UPDATE SET
          last_fired_at = excluded.last_fired_at
        WHERE excluded.last_fired_at > alert_trigger_last_fire.last_fired_at
        """,
        (trig_id, int(ts))
    )

def _insert_call_record(db, radio_system_id: int, talkgroup: str, local_audio_path: str,
                        local_debug_audio_path, audio_duration: float, start_time: int, merged: bool,
                        talkgroup_name: str = None) -> int:
    ins = db.execute_commit(
        """
        INSERT INTO call_records
        (radio_system_id, talkgroup, talkgroup_name, file_path, debug_file_path,
         duration_s, start_epoch_s, merged_from_stub)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (radio_system_id, talkgroup, talkgroup_name, local_audio_path, local_debug_audio_path, audio_duration, start_time, int(merged)),
        return_row_id=True
    )
    if not ins["success"]:
        raise RuntimeError(ins["message"])
    return ins["result"]

def _insert_trigger_fire(db, call_id: int, trig_id: int, ts: int):
    db.execute_commit(
        """
        INSERT INTO trigger_fires(call_id, alert_trigger_id, fired_at_epoch_s)
        VALUES (?, ?, ?)
        """,
        (call_id, trig_id, ts)
    )

def _build_audio_url(rel_path: str) -> str:
    """
    Convert the *static* file path returned by _write_mp3(..) into a
    fully qualified URL, ensuring the protocol is present.
    Example:
        https://example.com/static/audio/2025/07/rs2_abcd.mp3
    """
    # Prefer configured base, else request.host_url
    base = current_app.config.get("EXTERNAL_BASE_URL") or request.host_url.rstrip("/")

    # Ensure base has protocol (default to https)
    parsed = urlparse(base)
    if not parsed.scheme:
        base = f"https://{base.lstrip('/')}"
    elif parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme in base: {parsed.scheme}")

    rel = rel_path.lstrip("/")  # ensure leading slash stripped
    return f"{base}/{rel}"

# ----------------------------------------------------------------------
#  WebRTC-VAD: speech statistics  (sec & ratio)
# ----------------------------------------------------------------------
def speech_stats_webrtc(seg: AudioSegment,
                        frame_ms: int = 20) -> tuple[float, float]:
    """
    Return (speech_sec, speech_ratio) for *seg* using WebRTC-VAD.
        • speech_sec   – total seconds of voiced frames
        • speech_ratio – voiced-frames / total-frames
    """
    if len(seg) == 0:
        return 0.0, 0.0

    seg = seg.set_frame_rate(16_000).set_channels(1).set_sample_width(2)
    pcm = seg.raw_data
    bytes_per_frame = int(16_000 * frame_ms / 1000) * 2

    voiced_frames = total_frames = 0
    for i in range(0, len(pcm), bytes_per_frame):
        frame = pcm[i:i + bytes_per_frame]
        if len(frame) < bytes_per_frame:
            break
        total_frames += 1
        if _webrtc_vad.is_speech(frame, 16_000):
            voiced_frames += 1

    speech_sec   = voiced_frames * frame_ms / 1000.0
    speech_ratio = voiced_frames / total_frames if total_frames else 0.0
    return speech_sec, speech_ratio

# ----------------------------------------------------------------------
#  STUB-CLASSIFICATION HELPER  (no redundant checks)
# ----------------------------------------------------------------------
def _is_tone_only_stub(
        detection_result   : ToneDetectionResult,
        audio_segment      : AudioSegment,
        system_upload_config: dict,
        route_logger,
) -> bool:
    """
    True  → treat upload as *stub* (tones only, to be merged later).

    Preconditions (already checked by caller):
        • split_enabled is True
        • at least one paging-tone set is present
    """
    spans = _tone_spans(detection_result)
    if not spans:
        route_logger.debug("[stub-chk] no tone spans → VOICE")
        return False

    last_end_s   = spans[-1][1]
    tail_seg     = audio_segment[int(last_end_s * 1000):]
    tail_len_s   = tail_seg.duration_seconds

    # ----------- Step 1: any speech at all? --------------------------
    speech_sec, speech_ratio = speech_stats_webrtc(tail_seg)
    has_speech = speech_sec > 0.0

    route_logger.debug(
        "[stub-chk] tail_len=%.2fs  speech_sec=%.2fs  speech_ratio=%.3f",
        tail_len_s, speech_sec, speech_ratio)

    if not has_speech:
        route_logger.debug("[stub-chk] tail has NO speech → STUB")
        return True                         # tones-only

    # ----------- Step 2: is voiced part long enough? -----------------
    min_voice = system_upload_config["tail_min_voice_sec"]
    if speech_sec < min_voice:
        route_logger.debug("[stub-chk] speech %.2fs < min_voice %.2fs → STUB",
                           speech_sec, min_voice)
        return True

    route_logger.debug("[stub-chk] speech ≥ min_voice → VOICE")
    return False


def _tone_spans(det: ToneDetectionResult) -> list[tuple[float, float]]:
    """Return *merged*, ordered tone-spans in seconds."""
    spans: list[tuple[float, float]] = []

    for g in (
            det.two_tone_result + det.long_result +
            det.hi_low_result + det.pulsed_result + det.dtmf_result
    ):
        s = _parse_to_float(g.get("start"))
        e = _parse_to_float(g.get("end"))
        # fallback: start + length
        if s is not None and e is None:
            length = _parse_to_float(g.get("length"))
            if length is None and "tone_a_length" in g and "tone_b_length" in g:
                length = (_parse_to_float(g["tone_a_length"]) or 0) + \
                         (_parse_to_float(g["tone_b_length"]) or 0)
            if length:
                e = s + length
        if s is not None and e is not None:
            spans.append((s, max(e, s)))

    if not spans:
        return []

    spans.sort(key=lambda p: p[0])
    merged = [list(spans[0])]          # [[start, end], …]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:         # overlap / touch
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [tuple(p) for p in merged]

def _vad_segments_webrtc(seg: AudioSegment,
                         frame_ms: int = 20,
                         min_segment_s: float = 0.25,
                         merge_gap_s: float = 0.10) -> list[tuple[str, float, float]]:
    """
    Return coalesced VAD segments as [(kind, start_s, end_s), ...],
    where kind ∈ {"voice","silence"}.

    - Uses WebRTC VAD (same _webrtc_vad instance).
    - Coalesces contiguous frames of the same class.
    - Drops tiny blips shorter than min_segment_s.
    - Merges neighboring segments separated by <= merge_gap_s (same kind).
    """
    if len(seg) == 0:
        return []

    # Prepare 16kHz mono 16-bit PCM for VAD
    seg = seg.set_frame_rate(16_000).set_channels(1).set_sample_width(2)
    pcm = seg.raw_data
    bytes_per_frame = int(16_000 * frame_ms / 1000) * 2  # 2 bytes/sample

    # 1) frame-level decisions
    frames: list[tuple[float, float, bool]] = []
    pos = 0
    idx = 0
    while pos + bytes_per_frame <= len(pcm):
        frame = pcm[pos:pos + bytes_per_frame]
        start_s = idx * (frame_ms / 1000.0)
        end_s   = start_s + (frame_ms / 1000.0)
        is_speech = _webrtc_vad.is_speech(frame, 16_000)
        frames.append((start_s, end_s, is_speech))
        pos += bytes_per_frame
        idx += 1
    if not frames:
        return []

    # 2) coalesce contiguous frames
    segs: list[tuple[str, float, float]] = []
    cur_kind = "voice" if frames[0][2] else "silence"
    cur_start = frames[0][0]
    cur_end = frames[0][1]

    for s, e, speech in frames[1:]:
        k = "voice" if speech else "silence"
        if k == cur_kind and abs(s - cur_end) <= 1e-9:
            cur_end = e
        else:
            segs.append((cur_kind, cur_start, cur_end))
            cur_kind, cur_start, cur_end = k, s, e
    segs.append((cur_kind, cur_start, cur_end))

    # 3) drop tiny blips
    segs = [(k, s, e) for (k, s, e) in segs if (e - s) >= min_segment_s]

    # 4) merge small gaps between same-kind neighbors
    if not segs:
        return []
    merged: list[tuple[str, float, float]] = [segs[0]]
    for k, s, e in segs[1:]:
        pk, ps, pe = merged[-1]
        if k == pk and (s - pe) <= merge_gap_s:
            merged[-1] = (pk, ps, e)
        else:
            merged.append((k, s, e))
    return merged


def _insert_vad_segments(db, call_id: int, segments: list[tuple[str, float, float]]) -> None:
    """
    Bulk insert VAD segments for a call. Safe no-op on empty list.
    """
    if not segments:
        return
    params = [(call_id, k, s, e) for (k, s, e) in segments]
    db.execute_many(
        """
        INSERT INTO call_vad_segments (call_id, kind, start_s, end_s)
        VALUES (?, ?, ?, ?)
        """,
        params
    )

def create_audio_filename(radio_system_id: int, talkgroup_id: int, start_time: int, is_debug = False) -> str:
    if is_debug:
        new_file_name = f"{radio_system_id}_{talkgroup_id}_{start_time}_debug.mp3"
    else:
        new_file_name = f"{radio_system_id}_{talkgroup_id}_{start_time}.mp3"

    return new_file_name

def save_call_transcript(db: PostgreSQLDatabase, call_id: int, resp: Dict[str, Any]) -> None:
    """
    Persist transcript header + per-segment rows using your SQLite wrapper.
    Atomic: begin() → writes → commit() (rollback on error).

    Expects a Whisper-like response:
      {
        "duration": float,
        "language": "en",
        "model": "large-v3",
        "text": "...",
        "segments": [
          { "id": int, "start": float, "end": float, "text": str, "words": [{start,end,word,...}, ...] },
          ...
        ],

        # OPTIONAL – attached by address extractor
        "address_extracted": {...},   # dict from ExtractedAddress.to_dict()
        "address_geocoded":  {...},   # dict from GeocodedAddress.to_dict()

        # OPTIONAL – attached by incident classifier
        # Either a dict or an IncidentClassification dataclass
        "incident_classification": {
          "category": "MOTOR_VEHICLE_CRASH",
          "categories_considered": [...],
          "raw_model_output": "..."
        }
      }
    """
    if not isinstance(resp, dict) or not resp:
        return

    model: Optional[str]      = resp.get("model")
    language: Optional[str]   = resp.get("language")
    duration: Optional[float] = resp.get("duration")
    text_full: str            = (resp.get("text") or "").strip()
    segments: List[Dict[str, Any]] = list(resp.get("segments") or [])
    has_words: bool           = any(bool(seg.get("words")) for seg in segments)

    # ---------- Address payloads (already normalized by the address service) ----------
    addr_extracted_obj = resp.get("address_extracted")
    addr_geocoded_obj  = resp.get("address_geocoded")

    address_extracted_json = (
        json.dumps(addr_extracted_obj, ensure_ascii=False)
        if addr_extracted_obj is not None
        else None
    )
    address_geocoded_json = (
        json.dumps(addr_geocoded_obj, ensure_ascii=False)
        if addr_geocoded_obj is not None
        else None
    )

    # ---------- Incident classification payload ----------
    incident_obj = resp.get("incident_classification")
    incident_category: Optional[str] = None
    incident_classification_json: Optional[str] = None

    # We'll also make a JSON-serializable version for raw_json
    incident_dict_for_raw: Optional[Dict[str, Any]] = None

    if incident_obj is not None:
        if is_dataclass(incident_obj):
            incident_dict = asdict(incident_obj)
        elif isinstance(incident_obj, dict):
            incident_dict = incident_obj
        else:
            # Last-ditch: coerce to a minimal dict
            incident_dict = {
                "category": getattr(incident_obj, "category", None),
                "raw_model_output": str(incident_obj),
            }

        incident_category = (incident_dict.get("category") or "") or None
        incident_classification_json = json.dumps(incident_dict, ensure_ascii=False)
        incident_dict_for_raw = incident_dict

    # ---------- Raw JSON snapshot ----------
    # Ensure everything in raw_json is JSON-serializable
    raw_payload: Dict[str, Any] = dict(resp)

    # Normalize incident_classification for raw_json if needed
    if incident_dict_for_raw is not None:
        raw_payload["incident_classification"] = incident_dict_for_raw

    raw_json = json.dumps(raw_payload, ensure_ascii=False)

    header_sql = """
                 INSERT INTO call_transcripts
                 (call_id,
                  model,
                  language,
                  duration_s,
                  text_full,
                  has_words,
                  raw_json,
                  address_extracted_json,
                  address_geocoded_json,
                  incident_category,
                  incident_classification_json)
                 VALUES
                     (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(call_id) DO UPDATE SET
            model                      = excluded.model,
            language                   = excluded.language,
            duration_s                 = excluded.duration_s,
            text_full                  = excluded.text_full,
            has_words                  = excluded.has_words,
            raw_json                   = excluded.raw_json,
            address_extracted_json     = excluded.address_extracted_json,
            address_geocoded_json      = excluded.address_geocoded_json,
            incident_category          = excluded.incident_category,
            incident_classification_json = excluded.incident_classification_json \
                 """

    header_params = (
        call_id,
        model,
        language,
        duration,
        text_full,
        int(has_words),
        raw_json,
        address_extracted_json,
        address_geocoded_json,
        incident_category,
        incident_classification_json,
    )

    seg_sql = """
              INSERT INTO call_transcript_segments
                  (call_id, seg_index, start_s, end_s, text, word_count, words_json)
              VALUES
                  (?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(call_id, seg_index) DO UPDATE SET
            start_s    = excluded.start_s,
            end_s      = excluded.end_s,
            text       = excluded.text,
            word_count = excluded.word_count,
            words_json = excluded.words_json \
              """

    seg_rows: List[Tuple] = []
    for i, seg in enumerate(segments):
        seg_index = int(seg.get("id", i))
        start_s   = float(seg.get("start") or 0.0)
        end_s     = float(seg.get("end") or start_s)
        text      = (seg.get("text") or "").strip()

        words = seg.get("words") or []
        # keep only start, end, word (drop probabilities etc)
        words_compact = [
            {
                "start": float(w.get("start", 0.0)),
                "end":   float(w.get("end",   w.get("start", 0.0))),
                "word":  str(w.get("word", "")),
            }
            for w in words
        ]
        word_count = len(words_compact)
        words_json = (
            json.dumps(words_compact, ensure_ascii=False)
            if words_compact
            else None
        )

        seg_rows.append(
            (call_id, seg_index, start_s, end_s, text, word_count, words_json)
        )

    # ---------- Atomic write ----------
    db.begin()
    try:
        r1 = db.execute_commit(
            header_sql,
            header_params,
            return_row_id=False,
            return_count=True,
        )
        if not r1.get("success"):
            raise RuntimeError(f"header upsert failed: {r1.get('message')}")

        if seg_rows:
            r2 = db.execute_many(seg_sql, seg_rows)
            if not r2.get("success"):
                raise RuntimeError(f"segment upsert failed: {r2.get('message')}")

        db.commit()
    except Exception:
        db.rollback()
        raise


def _derive_town_hint_from_triggers(fired_trigger_data: List[Dict[str, Any]]) -> Optional[str]:
    """
    Derive a township/city hint from fired trigger names.

    Trigger names like "FIRE - Whitewater Region" or "EMS - Pembroke Base"
    contain municipality information. We strip common prefixes and join
    unique names to give the LLM geocoder better local context.

    Returns a comma-separated hint string or None if no triggers fired.
    """
    if not fired_trigger_data:
        return None

    towns = []
    seen = set()
    for trig in fired_trigger_data:
        name = trig.get("alert_trigger_name") or ""
        name = name.strip()
        if not name:
            continue
        # Strip common prefixes
        for prefix in ("FIRE - ", "EMS - "):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        if name and name not in seen:
            seen.add(name)
            towns.append(name)

    if not towns:
        return None

    return ", ".join(towns)


def _load_address_extraction_service(
        db: PostgreSQLDatabase,
        radio_system_id: int,
        route_logger,
) -> Optional[AddressExtractionService]:
    """
    Build an AddressExtractionService for this radio_system_id.

    Config source:
      - get_system_address_extraction_settings(db, radio_system_id, include_regions=True)

    This reuses the same logic/shape as the systems_address_extraction_settings
    API route, so there is a single source of truth for the config.
    """

    # ---- Load settings row via shared helper ----
    try:
        raw_res = get_system_address_extraction_settings(
            db,
            radio_system_id=radio_system_id,
            include_regions=True,
        )
    except Exception as e:
        route_logger.warning(
            "AddressExtraction: error loading settings for rsid=%s: %s",
            radio_system_id,
            e,
        )
        return None

    if not raw_res.get("success") or not raw_res.get("result"):
        route_logger.debug(
            "AddressExtraction: no settings row for rsid=%s; address extraction disabled",
            radio_system_id,
        )
        return None

    row = raw_res["result"]

    enabled_flag = int(row.get("address_extraction_enabled") or 0)
    if not enabled_flag:
        route_logger.debug(
            "AddressExtraction: address_extraction_enabled=0 for rsid=%s; disabled",
            radio_system_id,
        )
        return None

    # ---- Shape the payload exactly like your /address_extraction/settings GET ----
    address_payload = {
        "address_extraction_setting_id": row["address_extraction_setting_id"],
        "enabled": row["address_extraction_enabled"],
        "geocode_city": row["geocode_city"],
        "geocode_country": row["geocode_country"],
        "geocode_state": row["geocode_state"],
        "google_maps_api_key": row["google_maps_api_key"],
        "openai_api_key": row["openai_api_key"],
        "openai_model": row["openai_model"],
        "bounds_min_lat": row.get("bounds_min_lat"),
        "bounds_max_lat": row.get("bounds_max_lat"),
        "bounds_min_lng": row.get("bounds_min_lng"),
        "bounds_max_lng": row.get("bounds_max_lng"),
        "regions": row.get("regions", []),
        "cities": row.get("cities", []),
    }

    # Match the shape expected by AddressExtractionService.from_system_row(...)
    system_row = {"address_extraction": address_payload}

    try:
        svc = AddressExtractionService.from_system_row(system_row, logger=route_logger)
    except AddressExtractorError as e:
        route_logger.info(
            "AddressExtraction disabled/misconfigured for rsid=%s: %s",
            radio_system_id,
            e,
        )
        return None
    except Exception as e:
        route_logger.warning(
            "AddressExtraction: failed to init service for rsid=%s: %s",
            radio_system_id,
            e,
        )
        return None

    if not getattr(svc, "enabled", False):
        route_logger.debug(
            "AddressExtractionService is disabled for rsid=%s",
            radio_system_id,
        )
        return None

    return svc


def _maybe_extract_address_for_call(
        *,
        db: PostgreSQLDatabase,
        radio_system_id: int,
        call_id: int,
        transcript_response: Dict[str, Any],
        call_data: Dict[str, Any],
        route_logger,
        town_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    If possible, run address extraction on the transcript text for this call.

    Args:
        town_hint: Optional township/city hint derived from fired trigger
                   names (e.g. "Whitewater Region"). Passed to the LLM
                   address extractor to improve local context.

    Side effects (if we get a result):
      • call_data["address_extracted"]  -> dict
      • call_data["address_geocoded"]   -> dict
      • transcript_response["address_extracted"] / ["address_geocoded"]
        (so it is persisted in call_transcripts.raw_json)
    """
    text = (transcript_response.get("text") or "").strip()
    if not text:
        return None

    svc = _load_address_extraction_service(db, radio_system_id, route_logger)
    if not svc:
        return None

    if town_hint:
        route_logger.info(
            "Address extraction: call_id=%s using town_hint=%r",
            call_id, town_hint,
        )

    try:
        result = svc.extract_and_geocode(text, town_hint_override=town_hint)
    except AddressExtractorError as e:
        route_logger.warning(
            "Address extraction failed for call_id=%s: %s", call_id, e
        )
        return None
    except Exception as e:
        route_logger.warning(
            "Address extraction unexpected error for call_id=%s: %s", call_id, e
        )
        return None

    if not result:
        route_logger.debug(
            "Address extraction: no usable address for call_id=%s", call_id
        )
        return None

    extracted = result.get("extracted")
    geocoded = result.get("geocoded")

    extracted_dict: Optional[Dict[str, Any]] = (
        extracted.to_dict() if extracted is not None else None
    )
    geocoded_dict: Optional[Dict[str, Any]] = None

    if geocoded is not None:
        geocoded_dict = geocoded.to_dict()
        # Handy maps URL
        try:
            geocoded_dict["maps_url"] = (
                f"https://www.google.com/maps/search/?api=1&query="
                f"{geocoded.lat},{geocoded.lng}"
            )
        except Exception:
            pass

    # Attach to call_data in memory (for payload, logs, etc.)
    # (These names are exactly what build_context() expects.)
    call_data["address_extracted"] = extracted_dict
    call_data["address_geocoded"] = geocoded_dict

    # Also attach to transcript JSON so it persists with save_call_transcript()
    transcript_response["address_extracted"] = extracted_dict
    transcript_response["address_geocoded"] = geocoded_dict

    return {
        "extracted": extracted_dict,
        "geocoded": geocoded_dict,
    }

def _load_incident_classification_service(
        db: PostgreSQLDatabase,
        radio_system_id: int,
        route_logger,
) -> Optional[IncidentClassificationService]:

    cfg = get_systems(db, radio_system_id=radio_system_id, include_config=True)
    system_row = cfg["result"][0] if cfg and cfg.get("result") else None
    if not system_row:
        route_logger.warning(
            "Incident classification: no system row for radio_system_id=%s",
            radio_system_id,
        )
        return None

    try:
        return IncidentClassificationService.from_system_row(system_row, logger=route_logger)
    except Exception as e:
        route_logger.warning(
            "Incident classification: failed to build service for radio_system_id=%s: %s",
            radio_system_id,
            e,
        )
        return None


def _maybe_classify_incident_for_call(
        *,
        db: PostgreSQLDatabase,
        radio_system_id: int,
        call_id: int,
        transcript_response: Dict[str, Any],
        call_data: Dict[str, Any],
        route_logger,
) -> Optional[Dict[str, Any]]:
    text = (transcript_response.get("text") or "").strip()
    if not text:
        return None

    svc = _load_incident_classification_service(db, radio_system_id, route_logger)
    if not svc or not svc.enabled:
        return None

    try:
        result = svc.classify(text)
    except Exception as e:
        route_logger.warning(
            "Incident classification failed for call_id=%s: %s", call_id, e
        )
        return None

    result_dict = result.to_dict()

    call_data["incident_category"] = result.category
    call_data["incident_type"] = result.incident_type

    transcript_response["incident_classification"] = result_dict

    return {
        "category": result.category,
        "incident_type": result.incident_type,
        "classification": result_dict,
    }


@api_call_upload.route("/reprocess/<int:call_id>", methods=["POST"])
@token_or_login_required
def reprocess_call_tones(call_id):
    """Reprocess tones for an existing call."""
    import traceback
    
    route_logger = logging.getLogger('icad_dispatch.call_upload')
    route_logger.info("Reprocess tones for call_id=%s", call_id)
    
    db = current_app.config["db"]
    try:
        call_res = db.execute_query(
            "SELECT radio_system_id, talkgroup, file_path, start_epoch_s, duration_s FROM call_records WHERE call_id = ?",
            (call_id,),
            fetch_mode="one"
        )
        call_row = call_res.get("result") if call_res.get("success") else None

        if not call_row:
            return jsonify({"error": "Call not found"}), 404

        radio_system_id = call_row["radio_system_id"]
        audio_file = call_row["file_path"]

        audio_path = Path(audio_file)
        if not audio_path.is_absolute():
            audio_path = Path("static/audio") / audio_path

        if not audio_path.exists():
            return jsonify({"error": "Audio file not found"}), 404

        tone_cfg = _load_tone_cfg(db, radio_system_id)
        if not tone_cfg.get("tone_finder_enabled"):
            return jsonify({"error": "Tone finder not enabled for this system"}), 400

        audio_segment = AudioSegment.from_file(str(audio_path))

        detect_result = tone_detect(
            audio_segment,
            matching_threshold=tone_cfg["matching_threshold"],
            tone_a_min_length=tone_cfg["tone_a_min_length"],
            tone_b_min_length=tone_cfg["tone_b_min_length"],
            fe_snr_above_noise_db=tone_cfg["fe_snr_above_noise_db"],
            two_tone_max_gap_between_a_b=0.5,
            two_tone_bw_hz=25.0,
            two_tone_min_pair_separation_hz=tone_cfg["two_tone_min_pair_separation_hz"],
            hi_low_interval=tone_cfg["hi_low_interval"],
            hi_low_min_alternations=tone_cfg["hi_low_min_alternations"],
            hi_low_tone_bw_hz=25.0,
            hi_low_min_pair_separation_hz=25.0,
            long_tone_min_length=tone_cfg["long_tone_min_length"],
            long_tone_bw_hz=25.0,
            pulsed_bw_hz=20.0,
            pulsed_min_cycles=tone_cfg["pulsed_min_cycles"],
            pulsed_min_on_ms=tone_cfg["pulsed_min_on_ms"],
            pulsed_max_on_ms=tone_cfg["pulsed_max_on_ms"],
            pulsed_min_off_ms=tone_cfg["pulsed_min_off_ms"],
            pulsed_max_off_ms=tone_cfg["pulsed_max_off_ms"],
            dtmf_min_ms=tone_cfg.get("dtmf_min_ms", 100),
            dtmf_merge_ms=tone_cfg.get("dtmf_merge_ms", 75),
            dtmf_start_offset_ms=tone_cfg.get("dtmf_start_offset_ms", -20),
            dtmf_end_offset_ms=tone_cfg.get("dtmf_end_offset_ms", 20),
            dtmf_sequence_gap_s=tone_cfg.get("dtmf_sequence_gap_s", 0.3),
        )

        # Delete existing tone events (cascade handles tone_trigger_map via FK)
        db.execute_commit("DELETE FROM call_tone_events WHERE call_id = ?", (call_id,))

        # Persist newly detected tones
        detect_has_tones = bool(
            detect_result.two_tone_result or detect_result.long_result
            or detect_result.hi_low_result or detect_result.pulsed_result
            or detect_result.dtmf_result or detect_result.mdc_result
        )

        tone_count = 0
        if detect_has_tones:
            # Count tones for response
            tone_count = (
                len(detect_result.two_tone_result)
                + len(detect_result.long_result)
                + len(detect_result.hi_low_result)
                + len(getattr(detect_result, "pulsed_result", []))
                + len(detect_result.mdc_result)
                + len(detect_result.dtmf_result)
            )

            # Re-evaluate triggers with new tone detection
            trigger_data = _load_triggers(db, radio_system_id)
            fired_trigger_data: list[dict] = []
            matched_tone_ids: set[str] = set()
            tone_ids_by_trig: dict[int, set[str]] = {}

            now_ts = time.time()
            for trig in trigger_data:
                if _cooling_down(trig, now_ts):
                    continue
                if not _tg_allows_trigger(trig, call_row["talkgroup"]):
                    continue
                hits = _tones_matching_trigger(trig, detect_result)
                if hits:
                    matched_tone_ids.update(hits)
                    tone_ids_by_trig[trig["alert_trigger_id"]] = hits
                    fired_trigger_data.append(trig)
                    _set_trigger_fired(db, trig["alert_trigger_id"], now_ts)

            # Deduplicate fired triggers
            seen_ids: set[int] = set()
            unique_fired: list[dict] = []
            for trig in fired_trigger_data:
                tid = trig.get("alert_trigger_id")
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                unique_fired.append(trig)
            fired_trigger_data = unique_fired

            # Persist tones with trigger links
            _persist_tone_sets(db, call_id, detect_result,
                               matched_tone_ids, fired_trigger_data, tone_ids_by_trig)

            # Re-insert trigger_fires rows
            for trig in fired_trigger_data:
                _insert_trigger_fire(db, call_id, trig["alert_trigger_id"], int(now_ts))

            # Re-dispatch notifications if triggers fired
            if fired_trigger_data:
                route_logger.info("Re-dispatching %d triggers for call_id=%s",
                                  len(fired_trigger_data), call_id)

                # Build payload like the original upload
                talkgroup = call_row["talkgroup"]
                start_epoch = call_row["start_epoch_s"]
                audio_duration = call_row["duration_s"]
                file_path = call_row["file_path"]
                storage_cfg = _load_storage_cfg(db, radio_system_id) or {}
                audio_url = file_path if file_path.startswith("http") else f"/audio/{file_path.replace('static/audio/', '')}"

                payload = {
                    "call_id": call_id,
                    "radio_system_id": radio_system_id,
                    "talkgroup": talkgroup,
                    "talkgroup_name": "",
                    "duration_s": audio_duration,
                    "start_epoch_s": int(start_epoch),
                    "audio_url": audio_url,
                    "tone_detect": {
                        "two_tone": detect_result.two_tone_result,
                        "long_tone": detect_result.long_result,
                        "hi_low_tone": detect_result.hi_low_result,
                        "pulsed_tone": detect_result.pulsed_result,
                        "dtmf_tone": detect_result.dtmf_result,
                        "mdc_tone": detect_result.mdc_result,
                    },
                }

                # Get transcript for dispatch
                transcript_res = db.execute_query(
                    "SELECT text_full FROM call_transcripts WHERE call_id = ?",
                    (call_id,), fetch_mode="one"
                )
                transcript_text = None
                transcript_segments = None
                if transcript_res.get("success") and transcript_res.get("result"):
                    transcript_text = transcript_res["result"].get("text_full")

                _dispatch_triggers(
                    db,
                    fired_trigger_data,
                    payload,
                    detect_result,
                    transcript_text=transcript_text,
                    transcript_segments=transcript_segments,
                    tz=current_app.config["TIMEZONE"],
                )

        route_logger.info("Reprocessed %d tones for call_id=%s (triggers_fired=%d)",
                          tone_count, call_id, len(fired_trigger_data) if detect_has_tones else 0)

        return jsonify({
            "success": True,
            "call_id": call_id,
            "tone_count": tone_count,
            "triggers_fired": len(fired_trigger_data) if detect_has_tones else 0,
        })
        
    except Exception as e:
        route_logger.error("Reprocess failed for call_id=%s: %s\n%s", call_id, e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500
