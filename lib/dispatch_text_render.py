# lib/dispatch_text_render.py
from __future__ import annotations

import re
from html import unescape
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger("icad_dispatch.dispatch_render")

# ─────────────────────────────────────────────────────────────────────────────
# Canonical text helpers (single source of truth)
# Supports {x} tokens; also normalizes {{x}} → {x}
# Unknown tokens are left as-is (e.g., "{missing_key}")
# ─────────────────────────────────────────────────────────────────────────────
_DOUBLE_BRACE = re.compile(r"\{\{(\s*[\w\.]+\s*)\}\}")

class _SafeDict(dict):
    def __missing__(self, key):  # leave unknown token visible
        return "{" + key + "}"

def expand_template(template: str | None, ctx: Dict[str, Any]) -> str:
    """Render {x}; also accepts {{x}} by normalizing to {x}. Unknown keys are left as-is."""
    if not template:
        return ""
    s = _DOUBLE_BRACE.sub(lambda m: "{" + m.group(1).strip() + "}", str(template))
    try:
        return s.format_map(_SafeDict(ctx))
    except Exception:
        return s


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    s = str(html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", "", s)
    s = re.sub(r"(?is)<script.*?>.*?</script>", "", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return unescape(s).strip()

# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_hms(sec: float | int) -> str:
    total = int(round(sec or 0))
    h, r = divmod(total, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def _ts_local(epoch_s: int | None, tz: str = "America/New_York") -> Tuple[str, str, str, str, str]:
    """
    Returns (time_12, time_24, timestamp_local, iso_utc, timestamp_compact)
      - time_12           e.g., "5:36:11 PM"
      - time_24           e.g., "17:36:11"
      - timestamp_readable   e.g., "15:41 Nov 08 2025"
      - iso_local         e.g., "2025-11-06T22:36:11+05:00"
      - iso_utc           e.g., "2025-11-06T22:36:11+00:00"
    """
    if not epoch_s:
        epoch_s = int(datetime.now(tz=timezone.utc).timestamp())
    dt_utc = datetime.fromtimestamp(int(epoch_s), tz=timezone.utc)
    dt_loc = dt_utc.astimezone(ZoneInfo(tz))

    t12_loc = dt_loc.strftime("%I:%M:%S %p").lstrip("0")
    t24_loc = dt_loc.strftime("%H:%M:%S")
    ts_local = dt_loc.strftime("%H:%M %b %d %Y")

    return t12_loc, t24_loc, ts_local, dt_loc.isoformat(timespec="seconds"), dt_utc.isoformat(timespec="seconds")

def _ext_swap(url: str, old: str, new: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    return url[:-len(old)] + new if url.endswith(old) else url

def _list_names(fired_trigger_data: List[dict]) -> List[str]:
    out: List[str] = []
    for t in fired_trigger_data or []:
        name = t.get("alert_trigger_name")
        if name:
            out.append(str(name))
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Tone summaries: robust to either object or dict shape
# ─────────────────────────────────────────────────────────────────────────────
def summarize_tones(detect_result: Any) -> str:
    """
    Build a compact, human-readable summary of tones detected.

    Handles:
      • ToneDetectionResult instance
      • ToneDetectionResult.__dict__ (fields ending with *_result)
      • dict like call_data["tone_detect"] with:
          {
              "two_tone":  [...],
              "long_tone": [...],
              "hi_low_tone": [...],
              "pulsed_tone": [...],
              "mdc_tone":  [...],
              "dtmf_tone": [...],
          }

    Example output:
      "2TONE: A=1153.8/1.00s B=879.0/3.05s • LONG: 1234.0/4.00s • PULSED: 1500Hz×8 • MDC: 2 hits • DTMF: 123#"
    """
    if not detect_result:
        return ""

    # ───────────────────────── normalize shape ─────────────────────────
    # Support either a dict or ToneDetectionResult object
    if isinstance(detect_result, dict):
        data = detect_result
        two_tone_hits = data.get("two_tone") or data.get("two_tone_result") or []
        long_hits     = data.get("long_tone") or data.get("long_result") or []
        hi_low_hits   = data.get("hi_low_tone") or data.get("hi_low_result") or []
        pulsed_hits   = data.get("pulsed_tone") or data.get("pulsed_result") or []
        mdc_hits      = data.get("mdc_tone") or data.get("mdc_result") or []
        dtmf_hits     = data.get("dtmf_tone") or data.get("dtmf_result") or []
    else:
        # Assume ToneDetectionResult-like object
        two_tone_hits = (
                getattr(detect_result, "two_tone_result", None)
                or getattr(detect_result, "two_tone", None)
                or []
        )
        long_hits = (
                getattr(detect_result, "long_result", None)
                or getattr(detect_result, "long_tone", None)
                or []
        )
        hi_low_hits = (
                getattr(detect_result, "hi_low_result", None)
                or getattr(detect_result, "hi_low_tone", None)
                or []
        )
        pulsed_hits = (
                getattr(detect_result, "pulsed_result", None)
                or getattr(detect_result, "pulsed_tone", None)
                or []
        )
        mdc_hits = (
                getattr(detect_result, "mdc_result", None)
                or getattr(detect_result, "mdc_tone", None)
                or []
        )
        dtmf_hits = (
                getattr(detect_result, "dtmf_result", None)
                or getattr(detect_result, "dtmf_tone", None)
                or []
        )

    # Ensure lists
    two_tone_hits = list(two_tone_hits or [])
    long_hits     = list(long_hits or [])
    hi_low_hits   = list(hi_low_hits or [])
    pulsed_hits   = list(pulsed_hits or [])
    mdc_hits      = list(mdc_hits or [])
    dtmf_hits     = list(dtmf_hits or [])

    parts: list[str] = []

    # ───────────────────────── helpers ─────────────────────────

    def _safe_float(x, default=None):
        try:
            return float(x)
        except Exception:
            return default

    # 2-TONE (Quick Call) summary
    if two_tone_hits:
        frags: list[str] = []
        for p in two_tone_hits[:3]:
            det = p.get("detected") or []
            a = None
            b = None
            if len(det) >= 2:
                a, b = det[0], det[1]
            if a is None:
                a = (
                        p.get("freq_a_hz")
                        or p.get("tone_a_hz")
                        or p.get("freq_a")
                        or ""
                )
            if b is None:
                b = (
                        p.get("freq_b_hz")
                        or p.get("tone_b_hz")
                        or p.get("freq_b")
                        or ""
                )

            la = (
                    p.get("tone_a_length")
                    or p.get("length_a_s")
                    or p.get("min_len_a_s")
            )
            lb = (
                    p.get("tone_b_length")
                    or p.get("length_b_s")
                    or p.get("min_len_b_s")
            )

            la_f = _safe_float(la)
            lb_f = _safe_float(lb)
            if la_f is not None and lb_f is not None:
                frags.append(f"A={a}/{la_f:.2f}s B={b}/{lb_f:.2f}s")
            else:
                frags.append(f"A={a} B={b}")

        if len(two_tone_hits) > 3:
            frags.append(f"+{len(two_tone_hits) - 3} more")
        parts.append("2TONE: " + " | ".join(frags))

    # LONG tone summary
    if long_hits:
        frags: list[str] = []
        for p in long_hits[:3]:
            freq = (
                    p.get("freq_hz")
                    or p.get("tone_hz")
                    or p.get("center_hz")
                    or p.get("freq")
                    or ""
            )

            dur = (
                    p.get("length")
                    or p.get("length_s")
                    or p.get("duration")
                    or p.get("tone_length")
            )
            dur_f = _safe_float(dur)
            if dur_f is None:
                # Fallback: derive from start/end if present
                start = _safe_float(p.get("start"))
                end = _safe_float(p.get("end"))
                if start is not None and end is not None:
                    dur_f = max(0.0, end - start)

            if dur_f is not None:
                frags.append(f"{freq}/{dur_f:.2f}s")
            else:
                frags.append(str(freq))

        if len(long_hits) > 3:
            frags.append(f"+{len(long_hits) - 3} more")
        parts.append("LONG: " + " | ".join(frags))

    # HI/LOW warble summary
    if hi_low_hits:
        frags: list[str] = []
        for p in hi_low_hits[:3]:
            hi = (
                    p.get("hi_freq_hz")
                    or p.get("hi_hz")
                    or p.get("freq_hi_hz")
                    or p.get("hi_freq")
                    or ""
            )
            lo = (
                    p.get("low_freq_hz")
                    or p.get("low_hz")
                    or p.get("freq_low_hz")
                    or p.get("low_freq")
                    or ""
            )
            alts = p.get("alternations") or p.get("alt_count") or p.get("cycles")
            if alts is not None:
                frags.append(f"{hi}/{lo} ({alts} alts)")
            else:
                frags.append(f"{hi}/{lo}")

        if len(hi_low_hits) > 3:
            frags.append(f"+{len(hi_low_hits) - 3} more")
        parts.append("HI/LOW: " + " | ".join(frags))

    # PULSED single-tone summary
    if pulsed_hits:
        frags: list[str] = []
        for p in pulsed_hits[:3]:
            freq = (
                    p.get("center_hz")
                    or p.get("freq_hz")
                    or p.get("tone_hz")
                    or p.get("freq")
                    or ""
            )
            cycles = p.get("cycles") or p.get("cycle_count")
            if cycles is not None:
                frags.append(f"{freq}Hz×{cycles}")
            else:
                frags.append(f"{freq}Hz")

        if len(pulsed_hits) > 3:
            frags.append(f"+{len(pulsed_hits) - 3} more")
        parts.append("PULSED: " + " | ".join(frags))

    # MDC summary
    if mdc_hits:
        codes: list[str] = []
        for p in mdc_hits[:3]:
            code = (
                    p.get("decoded")
                    or p.get("message")
                    or p.get("raw")
                    or p.get("id")
                    or p.get("code")
            )
            if code is not None:
                codes.append(str(code))
        if codes:
            if len(mdc_hits) > 3:
                codes.append(f"+{len(mdc_hits) - 3} more")
            parts.append("MDC: " + ", ".join(codes))
        else:
            parts.append(f"MDC: {len(mdc_hits)} hits")

    # DTMF summary
    if dtmf_hits:
        sequences: list[str] = []
        for p in dtmf_hits[:3]:
            seq = (
                    p.get("digits")
                    or p.get("sequence")
                    or p.get("raw")
                    or p.get("code")
            )
            if seq is not None:
                sequences.append(str(seq))
        if sequences:
            if len(dtmf_hits) > 3:
                sequences.append(f"+{len(dtmf_hits) - 3} more")
            parts.append("DTMF: " + " | ".join(sequences))
        else:
            parts.append(f"DTMF: {len(dtmf_hits)} hits")

    return " • ".join(parts)

# ─────────────────────────────────────────────────────────────────────────────
# Context builder (single source of truth for placeholders)
# ─────────────────────────────────────────────────────────────────────────────
def build_context(
        system_row: dict,
        payload: dict,
        fired_trigger_data: List[dict],
        *,
        detect_result: Any = None,
        transcript_text: str | None = None,
        transcript_segments: List[dict] | None = None,
        tz: str = "America/New_York",
) -> Dict[str, Any]:
    """
    Produces a dict with all placeholders you asked for, aligned to your logs.
    Keys (selection):

    """

    sid = int(system_row.get("radio_system_id"))
    system_decimal = system_row.get("system_decimal")
    system_name = system_row.get("system_name", "")
    stream_url = (system_row.get("stream_url") or "")
    tg_raw = str(payload.get("talkgroup", ""))
    tg_name = str(payload.get("talkgroup_name", ""))

    duration_s = float(payload.get("duration_s", 0.0) or 0.0)
    start_epoch = int(payload.get("start_epoch_s") or 0)
    audio_url = payload.get("audio_url") or ""

    t12, t24, ts_local, iso_local, iso_utc = _ts_local(start_epoch, tz=tz)

    trig_names = _list_names(fired_trigger_data)
    trigger_list = ", ".join(trig_names)

    trigger_list_lines = "\n".join(trig_names)

    # ───────────────────── Address data (from payload) ─────────────────────
    addr_ex = payload.get("address_extracted") or {}
    addr_geo = payload.get("address_geocoded") or {}

    # If they came across the wire as JSON strings, try to decode
    if isinstance(addr_ex, str):
        try:
            addr_ex = json.loads(addr_ex) or {}
        except Exception:
            addr_ex = {}
    if isinstance(addr_geo, str):
        try:
            addr_geo = json.loads(addr_geo) or {}
        except Exception:
            addr_geo = {}

    # ───────────── Extracted (LLM) address pieces ─────────────
    ex_raw     = (addr_ex.get("raw_text") or "").strip()
    ex_street  = (addr_ex.get("street") or "").strip()
    ex_city    = (addr_ex.get("city") or "").strip()
    ex_county  = (addr_ex.get("county") or "").strip()
    ex_state   = (addr_ex.get("state") or "").strip()
    ex_postal  = (addr_ex.get("postal_code") or "").strip()
    ex_country = (addr_ex.get("country") or "").strip()

    # Best “one line” extracted address
    ex_line_parts = [ex_street, ex_city, ex_state, ex_postal]
    ex_line = ex_raw or ", ".join(p for p in ex_line_parts if p)

    # ───────────── Geocoded address pieces (Google) ─────────────
    geo_formatted = (addr_geo.get("formatted_address")
                     or addr_geo.get("raw_text")
                     or "").strip()
    geo_city    = (addr_geo.get("city")
                   or addr_geo.get("locality")
                   or "").strip()
    geo_county  = (addr_geo.get("county") or "").strip()
    geo_state   = (addr_geo.get("state")
                   or addr_geo.get("administrative_area")
                   or "").strip()
    geo_postal  = (addr_geo.get("postal_code") or addr_geo.get("zip") or "").strip()
    geo_country = (addr_geo.get("country") or "").strip()
    geo_lat     = addr_geo.get("lat", addr_geo.get("latitude"))
    geo_lng     = addr_geo.get("lng", addr_geo.get("longitude"))
    geo_maps    = (addr_geo.get("maps_url") or "").strip()

    try:
        geo_lat = float(geo_lat) if geo_lat is not None else None
    except Exception:
        geo_lat = None
    try:
        geo_lng = float(geo_lng) if geo_lng is not None else None
    except Exception:
        geo_lng = None

    # Best “one line” geocoded address
    geo_line_parts = [
        geo_formatted,
        ", ".join(p for p in [geo_city, geo_state, geo_postal] if p),
    ]
    geo_line = next((p for p in geo_line_parts if p), "")

    # ───────────── Unified address_* fields (GEOCODED > EXTRACTED) ─────────────
    has_geo = bool(
        geo_line or geo_formatted or geo_lat is not None or geo_lng is not None
    )
    has_ex = bool(ex_line or ex_raw or ex_city or ex_state or ex_postal or ex_country)

    if has_geo:
        address_source   = "GEOCODED"
        # Use *only* geocoded fields for normalized pieces so they remain self-consistent.
        address_line     = geo_line or ex_line
        address_city     = geo_city or ""
        address_county   = geo_county or ""
        address_state    = geo_state or ""
        address_postal   = geo_postal or ""
        # Country is safe to fall back from LLM if Google didn't give one
        address_country  = geo_country or ex_country
        address_lat      = geo_lat
        address_lng      = geo_lng
        address_maps_url = geo_maps
        # "raw" for the unified view: prefer geocoded text
        address_raw      = geo_formatted or geo_line or ex_raw or ex_line

        address_obj      = {
            "source": address_source,
            "line": address_line,
            "raw": address_raw,
            "city": address_city,
            "county": address_county,
            "state": address_state,
            "postal": address_postal,
            "country": address_country,
            "lat": address_lat,
            "lng": address_lng,
            "maps_url": address_maps_url,
        }

    elif has_ex:
        address_source   = "EXTRACTED"
        address_line     = ex_line
        address_city     = ex_city
        address_county   = ex_county
        address_state    = ex_state
        address_postal   = ex_postal
        address_country  = ex_country
        address_lat      = None
        address_lng      = None
        address_maps_url = ""
        address_raw      = ex_raw
        address_obj      = {
            "source": address_source,
            "line": address_line,
            "raw": address_raw,
            "city": address_city,
            "county": address_county,
            "state": address_state,
            "postal": address_postal,
            "country": address_country,
            "lat": None,
            "lng": None,
            "maps_url": "",
        }
    else:
        address_source   = "NONE"
        address_line     = ""
        address_city     = ""
        address_county   = ""
        address_state    = ""
        address_postal   = ""
        address_country  = ""
        address_lat      = None
        address_lng      = None
        address_maps_url = ""
        address_raw      = ""
        address_obj      = {
            "source": address_source,
            "line": "",
            "raw": "",
            "city": "",
            "county": "",
            "state": "",
            "postal": "",
            "country": "",
            "lat": None,
            "lng": None,
            "maps_url": "",
        }

    address_json = json.dumps(address_obj, ensure_ascii=False)

    # Transcript text fallback: derive from segments if needed
    if transcript_text is None and transcript_segments:
        try:
            transcript_text = " ".join(
                (s.get("text") or "").strip() for s in transcript_segments if s.get("text")
            )
        except Exception:
            transcript_text = ""

    # ───────────────────── Incident classification (ONLY 3 fields) ─────────────────────
    # We may receive incident data in a few shapes depending on the caller.
    # Prefer a real classification object if present, otherwise fall back to flat call_data fields.

    ic_raw = payload.get("incident_classification")

    if ic_raw is None:
        inc = payload.get("incident")
        if isinstance(inc, dict):
            ic_raw = inc.get("classification")

    if ic_raw is None:
        tr = payload.get("transcript")
        if isinstance(tr, dict):
            ic_raw = tr.get("incident_classification")

    # Legacy fallback (if you still have older payloads)
    if ic_raw is None:
        ic_raw = payload.get("incident_category_data")

    if isinstance(ic_raw, str):
        try:
            ic_raw = json.loads(ic_raw) or {}
        except Exception:
            ic_raw = {}

    # Flat fallbacks (your call_data sets these)
    tr = payload.get("transcript") if isinstance(payload.get("transcript"), dict) else {}

    incident_category = (
            payload.get("incident_category")
            or tr.get("incident_category")
            or (ic_raw.get("category") if isinstance(ic_raw, dict) else None)
            or ""
    ).strip()

    incident_type = (
            payload.get("incident_type")
            or (ic_raw.get("incident_type") if isinstance(ic_raw, dict) else None)
            or ""
    ).strip()

    incident_confidence = 0.0
    if isinstance(ic_raw, dict) and ic_raw.get("confidence") is not None:
        try:
            incident_confidence = float(ic_raw.get("confidence"))
        except Exception:
            incident_confidence = 0.0

    incident_obj = {
        "category": incident_category or "-",
        "incident_type": incident_type or "-",
        "confidence": incident_confidence,
    }
    incident_json = json.dumps(incident_obj, ensure_ascii=False)


    ctx: Dict[str, Any] = {
        # identification
        "radio_system_id": sid,
        "system_id": system_decimal,
        "system_name": system_name,

        # talkgroup (no TG table in this schema; label with the ID)
        "talkgroup_id": tg_raw,
        "talkgroup_name": tg_name,

        # incident classification
        "incident_category": incident_obj["category"],
        "incident_type": incident_obj["incident_type"],
        "incident_confidence": incident_obj["confidence"],
        "incident_json": incident_json,

        # triggers
        "trigger_names": trig_names,         # list form (useful for JSON)
        "trigger_list": trigger_list,        # CSV string
        "trigger_list_lines": trigger_list_lines,  # each trigger on its own line
        "trigger_count": len(trig_names),

        # audio & stream
        "audio_url": audio_url,
        "stream_url": stream_url,

        # timing
        "duration_s": round(duration_s, 2),
        "duration_hms": _fmt_hms(duration_s),
        "timestamp": start_epoch,
        "timestamp_12": t12,
        "timestamp_24": t24,
        "timestamp_readable": ts_local,
        "timestamp_iso_local": iso_local,
        "timestamp_iso_utc": iso_utc,

        # transcript
        "transcript": (transcript_text or "").strip(),
        "transcript_json": json.dumps(transcript_segments or [], ensure_ascii=False),

        "address_source": address_source,
        "address": address_raw,
        "address_city": address_city,
        "address_county": address_county,
        "address_state": address_state,
        "address_postal": address_postal,
        "address_country": address_country,
        "address_lat": address_lat,
        "address_lng": address_lng,
        "address_maps_url": address_maps_url,
        "address_json": address_json,

    }

    return ctx
