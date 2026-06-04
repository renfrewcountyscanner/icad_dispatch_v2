# lib/telegram_module.py
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

import requests

from lib.dispatch_text_render import expand_template, html_to_text

module_logger = logging.getLogger("icad_dispatch.telegram_module")

# ─────────────────────────────────────────────────────────────────────────────
# Datamodel
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TelegramSettings:
    enabled: bool
    bot_token: Optional[str]
    channel_id: Optional[str]
    message_body: Optional[str]     # caption template


# ─────────────────────────────────────────────────────────────────────────────
# Sender
# ─────────────────────────────────────────────────────────────────────────────

class TelegramSender:
    def __init__(self, settings: TelegramSettings, *, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.log = logger or module_logger

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.bot_token and self.settings.channel_id)

    @staticmethod
    def from_system_row(system_row: Dict[str, Any], *, logger: Optional[logging.Logger] = None) -> "TelegramSender":
        tcfg = (system_row or {}).get("telegram") or {}
        settings = TelegramSettings(
            enabled=bool(int(tcfg.get("enabled") or 0)),
            bot_token=(tcfg.get("bot_token") or tcfg.get("telegram_bot_token") or None),
            channel_id=(tcfg.get("channel_id") or tcfg.get("telegram_channel_id") or None),
            message_body=(tcfg.get("message_body") or "{timestamp}\\n{trigger_list}\\n{transcript}\\niCAD Dispatch"),
        )
        return TelegramSender(settings, logger=logger)

    def send(
        self,
        ctx: Dict[str, Any],
        *,
        fired_trigger_data: Optional[List[Dict[str, Any]]] = None,
        timeout_s: int = 20,
        max_retries: int = 2,
        voice_bitrate_k: int = 24,      # Opus bitrate
    ) -> bool:
        """
        Sends a voice message (Opus OGG) with caption built from placeholders.
        If any fired trigger has alert_trigger_enable_telegram=1, we send; otherwise we still
        send if system-level Telegram is enabled (this gating is optional).
        """
        if not self.enabled:
            self.log.debug("Telegram: disabled or missing token/channel; skipping")
            return False

        # Optional per-trigger gating
        if fired_trigger_data:
            if not any(bool(int(t.get("alert_trigger_enable_telegram") or 0)) for t in fired_trigger_data):
                self.log.info("Telegram: no fired triggers enabled for Telegram; skipping")
                return False

        audio_url = _coerce_str(ctx.get("audio_url"))
        if not audio_url:
            self.log.info("Telegram: no audio_url in context; skipping")
            return False

        caption = self._build_caption(ctx)
        voice_path = None
        try:
            voice_path = self._prepare_voice_file(audio_url, bitrate_k=voice_bitrate_k)
            if not voice_path:
                self.log.warning("Telegram: failed to prepare voice file")
                return False

            url = f"https://api.telegram.org/bot{self.settings.bot_token}/sendVoice"
            files = {"voice": open(voice_path, "rb")}
            data = {
                "chat_id": self.settings.channel_id,
                "caption": caption,
            }
            # Choose parse_mode automatically if looks like HTML
            if "<" in caption and ">" in caption:
                data["parse_mode"] = "HTML"

            return _post_with_retry(self.log, url, data=data, files=files,
                                    timeout_s=timeout_s, max_retries=max_retries)
        except Exception as e:
            self.log.warning("Telegram: send failed: %s", e)
            return False
        finally:
            try:
                if voice_path and os.path.exists(voice_path):
                    os.remove(voice_path)
            except Exception:
                pass

    # ───────────────────────── internals ─────────────────────────

    def _build_caption(self, ctx: Dict[str, Any]) -> str:
        """
        Expand placeholders and make Telegram-friendly text.
        Treat \\n in templates as real newlines.
        Clamp to Telegram's caption limits for voice (keep 1024 chars to be safe).
        """
        raw = self.settings.message_body or "{timestamp}\\n{trigger_list}\\n{transcript}\\niCAD Dispatch"
        expanded = expand_template(raw, ctx)
        # Support JSON-style "\n" in stored templates
        expanded = expanded.replace("\\n", "\n")
        # If it looks HTML-ish, keep; else strip any accidental tags just in case
        if "<" in expanded and ">" in expanded:
            text = expanded
        else:
            text = _to_plain_text(expanded)

        # Append shared map image URL if available
        map_url = ctx.get("map_image_url")
        if map_url:
            text = text + f"\n\n📍 Map: {map_url}"
            text = _clamp(text, 1024)

        return text

    def _prepare_voice_file(self, audio_url: str, *, bitrate_k: int = 24) -> Optional[str]:
        """
        Download the MP3 and convert to Opus OGG that Telegram expects for voice notes.
        Requires ffmpeg to be available in PATH.
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.log.warning("Telegram: ffmpeg not found in PATH; cannot make voice message")
            return None

        mp3_path = None
        ogg_path = None
        try:
            mp3_path = _download_to_temp(audio_url, suffix=".mp3", log=self.log)
            if not mp3_path:
                return None

            fd, ogg_path = tempfile.mkstemp(prefix="icad_voice_", suffix=".ogg")
            os.close(fd)
            # ffmpeg -i in.mp3 -c:a libopus -b:a 24k -ar 48000 -ac 1 out.ogg
            cmd = [
                ffmpeg, "-y",
                "-i", mp3_path,
                "-vn",
                "-c:a", "libopus",
                "-b:a", f"{int(bitrate_k)}k",
                "-ar", "48000",
                "-ac", "1",
                ogg_path,
            ]
            self._run_cmd(cmd, timeout=30)
            if not os.path.exists(ogg_path) or os.path.getsize(ogg_path) == 0:
                self.log.warning("Telegram: ffmpeg produced empty file")
                return None
            return ogg_path
        finally:
            try:
                if mp3_path and os.path.exists(mp3_path):
                    os.remove(mp3_path)
            except Exception:
                pass

    def _run_cmd(self, cmd: List[str], *, timeout: int) -> None:
        self.log.debug("Telegram: running %s", " ".join(cmd))
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed rc={proc.returncode} stderr={proc.stderr.decode('utf-8', 'ignore')[:400]}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _post_with_retry(
    log: logging.Logger,
    url: str,
    *,
    data: Dict[str, Any],
    files: Dict[str, Any],
    timeout_s: int,
    max_retries: int,
) -> bool:
    attempt = 0
    last_err: Optional[BaseException] = None
    while attempt <= max_retries:
        attempt += 1
        try:
            resp = requests.post(url, data=data, files=files, timeout=timeout_s)
            if 200 <= resp.status_code < 300:
                return True
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                log.warning("Telegram: 429 rate-limited; retrying in %.2fs (attempt %d/%d)",
                            retry_after, attempt, max_retries)
                time.sleep(retry_after)
                continue
            txt = (resp.text or "").strip().replace("\n", " ")
            log.warning("Telegram: HTTP %s: %s", resp.status_code, txt[:400])
            return False
        except Exception as e:
            last_err = e
            log.debug("Telegram: request error: %s (attempt %d/%d)", e, attempt, max_retries)
            time.sleep(0.5)
    if last_err:
        log.warning("Telegram: exhausted retries: %s", last_err)
    return False


def _retry_after_seconds(resp: requests.Response) -> float:
    try:
        data = resp.json()
        if isinstance(data, dict) and "parameters" in data:
            p = data["parameters"]
            if isinstance(p, dict) and "retry_after" in p:
                return float(p["retry_after"])
    except Exception:
        pass
    hdr = resp.headers.get("Retry-After")
    if not hdr:
        return 1.5
    try:
        val = float(hdr)
        return val / 1000.0 if val > 30_000 else val
    except Exception:
        return 1.5


def _download_to_temp(url: str, *, suffix: str, log: logging.Logger) -> Optional[str]:
    import requests
    try:
        with requests.get(url, stream=True, timeout=15) as r:
            r.raise_for_status()
            fd, path = tempfile.mkstemp(prefix="icad_dl_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
        return path
    except Exception as e:
        log.warning("Telegram: download failed for %s: %s", url, e)
        return None


def _to_plain_text(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    try:
        if "<" in s and ">" in s:
            s = html_to_text(s)
        s = unescape(s)
        return s.strip()
    except Exception:
        return s.strip()


def _clamp(s: str, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _coerce_str(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))
