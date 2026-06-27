# lib/n8n_module.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from lib.dispatch_text_render import expand_template

module_logger = logging.getLogger("icad_dispatch.n8n_module")


# ─────────────────────────────────────────────────────────────────────────────
# Data model (mirrors radio_system_n8n_settings; supports legacy key aliases)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class N8nSettings:
    enabled: bool
    webhook_url: str
    timeout_s: int
    jwt_passphrase: str
    jwt_issuer: str
    jwt_audience: str
    jwt_subject_tmpl: str
    jwt_ttl_s: int


class N8nSender:
    """
    Posts a JSON payload (derived from `ctx`) to an n8n Webhook node.
    Adds an Authorization: Bearer <HS256 JWT> header signed with the configured passphrase.
    """

    def __init__(self, settings: N8nSettings, *, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.log = logger or module_logger

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.webhook_url)

    # Factory from system_row (handles both canonical and aliased keys)
    @staticmethod
    def from_system_row(system_row: Dict[str, Any],
                        *, logger: Optional[logging.Logger] = None) -> "N8nSender":
        cfg = (system_row or {}).get("n8n") or (system_row or {}).get("radio_system_n8n_settings") or {}

        module_logger.debug(f"N8N System Config: {cfg}")

        def _get(*keys, default=None):
            for k in keys:
                if k in cfg and cfg[k] is not None:
                    return cfg[k]
            return default

        settings = N8nSettings(
            enabled=bool(cfg.get("enabled") or 0),
            webhook_url=cfg.get("webhook_url"),
            timeout_s=cfg.get("timeout_s") if cfg.get("timeout_s") is not None else 10,
            jwt_passphrase=cfg.get("jwt_passphrase"),
            jwt_issuer=cfg.get("jwt_issuer") or "icad_dispatch",
            jwt_audience=cfg.get("jwt_audience") or "n8n",
            jwt_subject_tmpl=cfg.get("jwt_subject_template") or "{radio_system_id}",
            jwt_ttl_s=cfg.get("jwt_ttl_s") if cfg.get("jwt_ttl_s") is not None else 300,
        )
        return N8nSender(settings, logger=logger)

    # Public API
    def send(
        self,
        ctx: Dict[str, Any],
        *,
        fired_trigger_data: Optional[List[Dict[str, Any]]] = None,
        only_if_any_trigger_enabled: bool = False,   # set True to require a trigger gate
        max_retries: int = 2,
    ) -> bool:
        """
        • If only_if_any_trigger_enabled=True, send only when at least one fired trigger has alert_trigger_enable_n8n=1.
        • Otherwise, send whenever system n8n is enabled.
        """
        if not self.enabled:
            self.log.debug("N8nSender: disabled or misconfigured; skipping")
            return False

        if only_if_any_trigger_enabled:
            any_gate = any(
                bool(int((t.get("alert_trigger_enable_n8n") or 0)))
                for t in (fired_trigger_data or [])
            )
            if not any_gate:
                self.log.debug("N8nSender: no fired trigger with alert_trigger_enable_n8n=1; skipping")
                return False

        payload = dict(ctx or {})
        # Ensure map_image_url is present in the payload
        if "map_image_url" not in payload:
            map_url = ctx.get("map_image_url")
            if map_url:
                payload["map_image_url"] = map_url

        headers = {
            "Content-Type": "application/json",
        }

        if self.settings.jwt_passphrase:
            try:
                token = self._build_jwt(ctx)
                headers["Authorization"] = f"Bearer {token}"
            except Exception as e:
                self.log.warning("N8nSender: JWT generation failed: %s — sending without auth", e)

        url = self.settings.webhook_url
        timeout_s = self.settings.timeout_s

        attempt = 0
        last_err: Optional[Exception] = None
        while attempt <= max_retries:
            attempt += 1
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout_s)
                if 200 <= resp.status_code < 300:
                    return True
                if resp.status_code == 429:
                    delay = _retry_after_seconds(resp)
                    self.log.warning("N8nSender: 429 rate-limited; retrying in %.2fs (attempt %d/%d)",
                                     delay, attempt, max_retries)
                    time.sleep(delay)
                    continue
                self.log.warning("N8nSender: HTTP %s %s", resp.status_code, (resp.text or "")[:400])
                return False
            except Exception as e:
                last_err = e
                self.log.debug("N8nSender: request error %r (attempt %d/%d)", e, attempt, max_retries)
                time.sleep(0.5)

        if last_err:
            self.log.warning("N8nSender: exhausted retries: %r", last_err)
        return False

    # ───────────────────────── internals ─────────────────────────

    def _build_payload(
        self,
        ctx: Dict[str, Any],
        fired_triggers: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Predetermined JSON shape, sourced from `ctx` (build_context) + fired trigger names.
        Keep keys stable for your n8n workflow mappings.
        """
        # Trigger names from ctx or derive from fired_triggers
        trigger_names = list(ctx.get("trigger_names") or [])
        if not trigger_names and fired_triggers:
            trigger_names = [str(t.get("alert_trigger_name") or "") for t in fired_triggers if t.get("alert_trigger_name")]
        trigger_names = [t for t in trigger_names if t]

        # Optional per-trigger gate info (for reference on the n8n side)
        gated_triggers = [t.get("alert_trigger_name") for t in fired_triggers if bool(int((t.get("alert_trigger_enable_n8n") or 0)))]
        gated_triggers = [g for g in gated_triggers if g]

        # Core payload
        out: Dict[str, Any] = {
            # Identity
            "radio_system_id": ctx.get("system_id"),
            "system_name": ctx.get("system_name"),
            "talkgroup_id": ctx.get("talkgroup_id"),
            "talkgroup_name": ctx.get("talkgroup_name"),

            # Triggers
            "trigger_names": trigger_names,
            "trigger_list": ctx.get("trigger_list"),
            "trigger_count": ctx.get("trigger_count"),
            "n8n_gated_triggers": gated_triggers,  # informational

            # Timing
            "timestamp": ctx.get("timestamp"),
            "timestamp_12": ctx.get("timestamp_12"),
            "timestamp_24": ctx.get("timestamp_24"),
            "timestamp_local": ctx.get("timestamp_local"),
            "timestamp_utc": ctx.get("timestamp_utc"),

            # Audio / stream
            "audio_url": ctx.get("audio_url"),
            "mp3_url": ctx.get("mp3_url"),
            "audio_wav_url": ctx.get("audio_wav_url"),
            "stream_url": ctx.get("stream_url"),

            # Content
            "duration_s": ctx.get("duration_s"),
            "duration_hms": ctx.get("duration_hms"),
            "transcript_text": _clamp_text(ctx.get("transcript_text") or ctx.get("transcript") or "", 6000),
            "tones_summary": ctx.get("tones_summary"),
            "transcript_segments": ctx.get("transcript_segments") or [],
        }
        return out

    def _build_jwt(self, ctx: Dict[str, Any]) -> str:
        """
        Create HS256 JWT with iss/aud/sub/iat/exp.
        `sub` supports placeholders via jwt_subject_tmpl expanded from ctx.
        """
        now = int(time.time())
        exp = now + int(self.settings.jwt_ttl_s)
        sub = expand_template(self.settings.jwt_subject_tmpl or "{radio_system_id}", ctx) or str(ctx.get("system_id", ""))

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.settings.jwt_issuer or "icad_dispatch",
            "aud": self.settings.jwt_audience or "n8n",
            "sub": str(sub),
            "iat": now,
            "exp": exp,
            "jti": uuid.uuid4().hex,
        }

        return _jwt_encode_hs256(header, payload, self.settings.jwt_passphrase)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _retry_after_seconds(resp: requests.Response) -> float:
    ra = resp.headers.get("Retry-After")
    try:
        if ra is not None:
            val = float(ra)
            return val / 1000.0 if val > 30_000 else val
    except Exception:
        pass
    return 1.5


def _clamp_text(s: Any, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt_encode_hs256(header: Dict[str, Any], payload: Dict[str, Any], secret: str) -> str:
    """
    Minimal HS256 JWT (no external deps). JSON is compact (no spaces), keys sorted for stability.
    """
    header_b = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signing_input = f"{_b64url(header_b)}.{_b64url(payload_b)}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode('ascii')}.{_b64url(sig)}"
