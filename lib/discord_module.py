# lib/discord_module.py
from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from lib.dispatch_text_render import expand_template, html_to_text

module_logger = logging.getLogger("icad_dispatch.discord_module")

DISCORD_MAX_ATTACH_BYTES = int(os.getenv("DISCORD_MAX_ATTACH_BYTES", str(8 * 1024 * 1024)))  # 8 MiB
DISCORD_MEDIA_TIMEOUT_S = int(os.getenv("DISCORD_MEDIA_TIMEOUT_S", "15"))

# ─────────────────────────────────────────────────────────────────────────────
# Datamodel
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DiscordField:
    embed_field_id: Optional[int]
    field_key: str
    field_label: str
    field_template: str
    field_inline: bool
    field_enabled: bool
    sort_order: int


@dataclass
class DiscordSettings:
    enabled: bool
    webhook_url: Optional[str]
    embed_title: Optional[str]
    embed_color: Optional[str]  # "#RRGGBB" or int-like string
    embed_footer: Optional[str]
    fields: List[DiscordField] = field(default_factory=list)
    render_map: bool = False
    attach_audio: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Sender
# ─────────────────────────────────────────────────────────────────────────────
class DiscordSender:
    def __init__(self, settings: DiscordSettings, *, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.log = logger or module_logger

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled) and bool(self.settings.webhook_url)

    # Factory from system_row
    @staticmethod
    def from_system_row(system_row: Dict[str, Any], *, logger: Optional[logging.Logger] = None) -> "DiscordSender":
        dcfg = (system_row or {}).get("discord") or {}
        raw_fields = dcfg.get("fields") or []
        fields: List[DiscordField] = []
        for f in raw_fields:
            try:
                fields.append(
                    DiscordField(
                        embed_field_id=f.get("embed_field_id"),
                        field_key=str(f.get("field_key") or ""),
                        field_label=str(f.get("field_label") or ""),
                        field_template=str(f.get("field_template") or ""),
                        field_inline=bool(int(f.get("field_inline"))) if isinstance(f.get("field_inline"), (int, str)) else bool(f.get("field_inline")),
                        field_enabled=bool(int(f.get("field_enabled"))) if isinstance(f.get("field_enabled"), (int, str)) else bool(f.get("field_enabled")),
                        sort_order=int(f.get("sort_order") or 0),
                    )
                )
            except Exception as e:
                (logger or module_logger).warning("DiscordSender: skipping bad field row %s: %s", f, e)

        settings = DiscordSettings(
            enabled=bool(int(dcfg.get("enabled") or 0)),
            webhook_url=(dcfg.get("webhook_url") or dcfg.get("discord_webhook_url") or None),
            embed_title=(dcfg.get("embed_title") or dcfg.get("discord_embed_title") or None),
            embed_color=(dcfg.get("embed_color") or dcfg.get("discord_embed_color") or None),
            embed_footer=(dcfg.get("embed_footer") or dcfg.get("discord_embed_footer") or None),
            render_map=bool(int(dcfg.get("render_map") or 0)),
            attach_audio=bool(int(dcfg.get("attach_audio") or 0)),
            fields=fields,
        )
        return DiscordSender(settings, logger=logger)

    # Public API
    def send(
            self,
            ctx: Dict[str, Any],
            *,
            content: Optional[str] = None,
            username: Optional[str] = None,
            avatar_url: Optional[str] = None,
            timeout_s: int = 12,
            max_retries: int = 2,
    ) -> bool:
        if not self.enabled:
            self.log.debug("DiscordSender: disabled or no webhook_url; skipping send")
            return False

        payload, files = self._build_payload_and_files(ctx, content=content, username=username, avatar_url=avatar_url)
        if not payload:
            self.log.debug("DiscordSender: empty payload; skipping send")
            return False

        try:
            return _post_with_retry(
                self.log,
                self.settings.webhook_url,
                payload,
                timeout_s=timeout_s,
                max_retries=max_retries,
                files=files,
            )
        except Exception as e:
            self.log.warning("DiscordSender: send failed: %s", e)
            return False

    # ───────────────────────── internals ─────────────────────────
    def _build_payload_and_files(
            self,
            ctx: Dict[str, Any],
            *,
            content: Optional[str],
            username: Optional[str],
            avatar_url: Optional[str],
    ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        embed = self._build_embed(ctx)

        files: Dict[str, Any] = {}
        file_index = 0

        # ── 1) Shared public map image URL
        map_url = ctx.get("map_image_url")
        if map_url:
            embed["image"] = {"url": map_url}

        # ── 2) Audio attachment (Discord will render an audio player for common types)
        if self.settings.attach_audio:
            try:
                audio_att = self._maybe_download_audio(ctx)
                if audio_att:
                    fname, data, ct = audio_att
                    files[f"files[{file_index}]"] = (fname, data, ct)
                    file_index += 1
            except Exception as e:
                self.log.debug("DiscordSender: audio attach skipped/failed: %s", e)

        payload: Dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = _to_discord_text(expand_template(content, ctx))
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url

        return payload, (files or None)


    def _build_payload(
        self,
        ctx: Dict[str, Any],
        *,
        content: Optional[str],
        username: Optional[str],
        avatar_url: Optional[str],
    ) -> Dict[str, Any]:
        embed = self._build_embed(ctx)
        if not embed.get("fields"):
            # still allow title-only embeds if you want; comment this out if desired
            self.log.debug("DiscordSender: zero non-empty fields; will still send header-only embed")

        payload: Dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = _to_discord_text(expand_template(content, ctx))
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url
        return payload

    def _build_embed(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        # Title
        title_raw = self.settings.embed_title or ""
        title = _clamp(expand_template(title_raw, ctx) if title_raw else "", 256)

        # Footer
        footer_raw = self.settings.embed_footer or ""
        footer_text = _clamp(_to_discord_text(expand_template(footer_raw, ctx) if footer_raw else ""), 2048)

        # Color → int
        color_int = _parse_color(self.settings.embed_color)

        # Fields: enabled → sort → expand → clean → clamp → drop empty → limit 25
        fields_out: List[Dict[str, Any]] = []
        for f in sorted(self.settings.fields, key=lambda x: (x.sort_order, x.embed_field_id or 0)):
            if not f.field_enabled:
                continue
            value = expand_template(f.field_template or "", ctx) or ""
            value = _to_discord_text(value)
            value = value.strip()
            if not value:
                continue
            name = _clamp(_to_discord_text(f.field_label or f.field_key or ""), 256)
            value = _clamp(value, 1024)
            fields_out.append({"name": name or f.field_key or "Field", "value": value, "inline": bool(f.field_inline)})
            if len(fields_out) >= 25:
                break

        embed: Dict[str, Any] = {}
        if title:
            embed["title"] = title
        if color_int is not None:
            embed["color"] = color_int
        if fields_out:
            embed["fields"] = fields_out
        # Timestamp (optional): if your ctx provides an ISO string, Discord will render it
        ts = ctx.get("timestamp_iso") or ctx.get("timestamp_ISO") or None
        if isinstance(ts, str) and ts:
            embed["timestamp"] = ts
        if footer_text:
            embed["footer"] = {"text": footer_text}
        return embed

    def _maybe_download_audio(self, ctx: Dict[str, Any]) -> Optional[tuple[str, bytes, str]]:
        """
        Download audio and return (filename, bytes, content_type) or None.
        """
        url = str(ctx.get("audio_url") or "").strip()
        if not url:
            return None

        data, ct = _download_url_limited(
            url,
            max_bytes=DISCORD_MAX_ATTACH_BYTES,
            timeout_s=DISCORD_MEDIA_TIMEOUT_S,
        )

        call_id = str(ctx.get("call_id") or ctx.get("id") or ctx.get("start_epoch_s") or "").strip()
        fallback = f"audio_{call_id}" if call_id else "audio"

        fname = _guess_filename(url, ct, fallback=fallback)
        return fname, data, ct


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _to_discord_text(s: Any) -> str:
    """
    Make a safe Discord-friendly string:
    • Convert simple HTML to plain text (re-use html_to_text if available)
    • Unescape HTML entities
    • Trim
    """
    if s is None:
        return ""
    s = str(s)
    try:
        # If HTML-ish, strip to text; fall back to a very light cleanup if html_to_text fails
        if "<" in s and ">" in s:
            try:
                s = html_to_text(s)
            except Exception:
                import re
                s = re.sub(r"(?i)<br\s*/?>", "\n", s)
                s = re.sub(r"<[^>]+>", "", s)
        s = unescape(s)
    finally:
        return s.strip()


def _clamp(s: str, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _parse_color(val: Optional[str]) -> Optional[int]:
    """
    Accepts "#RRGGBB", "0xRRGGBB", or base-10 string; returns int or None.
    """
    if not val:
        return None
    v = str(val).strip()
    try:
        if v.startswith("#"):
            return int(v[1:], 16)
        if v.startswith("0x") or v.startswith("0X"):
            return int(v, 16)
        return int(v)
    except Exception:
        return None


def _post_with_retry(
        log: logging.Logger,
        url: str,
        payload: Dict[str, Any],
        *,
        timeout_s: int,
        max_retries: int,
        files: Optional[Dict[str, Any]] = None,  # NEW
) -> bool:
    """
    POST to Discord webhook with light 429 handling.

    - If files is None: send JSON body (application/json)
    - If files provided: send multipart/form-data with payload_json + files[N]
    """
    attempt = 0
    last_err = None

    while attempt <= max_retries:
        attempt += 1
        try:
            if files:
                # Discord expects payload_json when sending multipart
                data = {"payload_json": json.dumps(payload, ensure_ascii=False)}
                resp = requests.post(url, data=data, files=files, timeout=timeout_s)
            else:
                resp = requests.post(url, json=payload, timeout=timeout_s)

            if 200 <= resp.status_code < 300:
                return True

            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                log.warning(
                    "DiscordSender: 429 rate-limited; retrying in %.2fs (attempt %d/%d)",
                    retry_after, attempt, max_retries
                )
                time.sleep(retry_after)
                continue

            body = _safe_body(resp)
            log.warning("DiscordSender: HTTP %s: %s", resp.status_code, body)
            return False

        except Exception as e:
            last_err = e
            log.debug("DiscordSender: request error: %s (attempt %d/%d)", e, attempt, max_retries)
            time.sleep(0.5)

    if last_err:
        log.warning("DiscordSender: exhausted retries: %s", last_err)
    return False

def _retry_after_seconds(resp: requests.Response) -> float:
    # Discord can return header Retry-After (seconds or ms) or JSON {"retry_after": seconds}
    try:
        # JSON hint
        data = resp.json()
        if isinstance(data, dict) and "retry_after" in data:
            return float(data["retry_after"])
    except Exception:
        pass
    # Header
    hdr = resp.headers.get("Retry-After")
    if not hdr:
        return 1.5
    try:
        # Most often seconds; if it's huge, treat as ms
        val = float(hdr)
        return val / 1000.0 if val > 30_000 else val
    except Exception:
        return 1.5


def _safe_body(resp: requests.Response) -> str:
    try:
        return json.dumps(resp.json(), ensure_ascii=False)[:500]
    except Exception:
        txt = (resp.text or "").strip().replace("\n", " ")
        return txt[:500]

def _download_url_limited(url: str, *, max_bytes: int, timeout_s: int) -> tuple[bytes, str]:
    """
    Download URL content up to max_bytes. Returns (bytes, content_type).
    Raises if exceeds limit or request fails.
    """
    with requests.get(url, stream=True, timeout=timeout_s) as r:
        r.raise_for_status()
        ct = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()

        # Fast-path if content-length known
        clen = r.headers.get("Content-Length")
        if clen:
            try:
                if int(clen) > max_bytes:
                    raise ValueError(f"Remote file too large ({clen} bytes) > max_bytes={max_bytes}")
            except Exception:
                pass

        buf = bytearray()
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"Remote file exceeded max_bytes={max_bytes}")
        return bytes(buf), ct


def _guess_filename(url: str, content_type: str, *, fallback: str) -> str:
    """
    Try to pick a stable filename based on URL path or content-type.
    """
    try:
        path = urlparse(url).path or ""
        base = os.path.basename(path)
        if base and "." in base and len(base) < 80:
            return base
    except Exception:
        pass

    ext = mimetypes.guess_extension(content_type or "") or ""
    if ext == ".jpe":
        ext = ".jpg"
    if not ext:
        # common Discord-friendly defaults
        if content_type == "audio/mpeg":
            ext = ".mp3"
        elif content_type == "audio/wav":
            ext = ".wav"
        elif content_type == "image/png":
            ext = ".png"

    return f"{fallback}{ext}"

