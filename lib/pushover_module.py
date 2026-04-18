# lib/pushover_module.py
from __future__ import annotations

import logging
import time
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from lib.dispatch_text_render import expand_template, html_to_text

module_logger = logging.getLogger("icad_dispatch.pushover_module")


# ─────────────────────────────────────────────────────────────────────────────
# Datamodels
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PushoverSettings:
    enabled: bool
    app_token: Optional[str]
    group_token: Optional[str]
    subject_tmpl: str
    body_tmpl: str
    sound: Optional[str]


@dataclass
class TriggerPushoverSettings:
    alert_trigger_id: int
    enabled: bool
    app_token: Optional[str]
    group_token: Optional[str]
    subject_tmpl: Optional[str]
    body_tmpl: Optional[str]
    sound: Optional[str]
    trigger_name: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Sender
# ─────────────────────────────────────────────────────────────────────────────

class PushoverSender:
    """
    Sends Pushover notifications:
      • System-level (“Systemwide Channel”) — one message per dispatch if enabled
      • Per-trigger (“Per Trigger Channel”) — one message per fired trigger if enabled AND tokens are set
    """
    PO_URL = "https://api.pushover.net/1/messages.json"

    def __init__(self, system_settings: PushoverSettings,
                 trigger_settings: List[TriggerPushoverSettings],
                 *, logger: Optional[logging.Logger] = None):
        self.sys = system_settings
        self.trg = trigger_settings or []
        self.log = logger or module_logger

    @property
    def enabled(self) -> bool:
        return bool(self.sys.enabled) or any(t.enabled for t in self.trg)

    # --------- Factories from your config/result shapes ---------

    @staticmethod
    def from_system_row(system_row: Dict[str, Any],
                        fired_trigger_rows: List[Dict[str, Any]] | None = None,
                        *, logger: Optional[logging.Logger] = None) -> "PushoverSender":
        """Build from get_systems(row) shape + fired_trigger_data list."""
        p = (system_row or {}).get("pushover") or {}
        sys_settings = PushoverSettings(
            enabled=bool(int(p.get("enabled") or p.get("pushover_enabled") or 0)),
            app_token=(p.get("app_token") or p.get("pushover_app_token") or None),
            group_token=(p.get("group_token") or p.get("pushover_group_token") or None),
            subject_tmpl=(p.get("subject") or p.get("pushover_subject") or "Dispatch Alert"),
            body_tmpl=(p.get("body") or p.get("pushover_body") or "{trigger_list}"),
            sound=(p.get("sound") or p.get("pushover_sound") or "pushover"),
        )

        triggers: List[TriggerPushoverSettings] = []
        for tr in (fired_trigger_rows or []):
            triggers.append(
                TriggerPushoverSettings(
                    alert_trigger_id=int(tr.get("alert_trigger_id")),
                    enabled=bool(int(tr.get("enable_pushover") or 0)),
                    app_token=(tr.get("pushover_app_token") or None),
                    group_token=(tr.get("pushover_group_token") or None),
                    subject_tmpl=(tr.get("pushover_subject") or None),
                    body_tmpl=(tr.get("pushover_body") or None),
                    sound=(tr.get("pushover_sound") or None),
                    trigger_name=str(tr.get("alert_trigger_name") or ""),
                )
            )
        return PushoverSender(sys_settings, triggers, logger=logger)

    # --------- Public API ---------

    def send_system(self, ctx: Dict[str, Any], *, timeout_s: int = 10) -> bool:
        """Send one system-wide push if enabled & tokens set."""
        if not self.sys.enabled:
            self.log.debug("Pushover(system): disabled; skipping")
            return False
        if not (self.sys.app_token and self.sys.group_token):
            self.log.warning("Pushover(system): missing app/group token; skipping")
            return False

        title = clamp(expand_template(self.sys.subject_tmpl, ctx) or "Dispatch Alert", 250)
        body_raw = expand_template(self.sys.body_tmpl, ctx) or ""
        message, html_flag = sanitize_for_pushover(body_raw)

        payload = self._base_payload(
            app_token=self.sys.app_token,
            group_token=self.sys.group_token,
            title=title,
            message=message,
            sound=self.sys.sound,
            html=1 if html_flag else 0,
            ctx=ctx,
        )
        return self._post_with_retry(payload, timeout_s=timeout_s)

    def send_for_triggers(self, ctx: Dict[str, Any], *,
                          timeout_s: int = 10) -> int:
        """
        Send one push per fired trigger that has its own Pushover config enabled
        and complete (group + app tokens). Returns count of successful sends.
        """
        sent = 0
        for tr in sorted(self.trg, key=lambda t: t.alert_trigger_id):
            if not tr.enabled:
                continue
            if not (tr.app_token and tr.group_token):
                self.log.debug("Pushover(trigger %s): enabled but missing tokens; skipping",
                               tr.alert_trigger_id)
                continue

            # Allow templates to reference {trigger_name}
            ctx_trg = dict(ctx)
            if tr.trigger_name and "trigger_name" not in ctx_trg:
                ctx_trg["trigger_name"] = tr.trigger_name

            title_tmpl = tr.subject_tmpl or self.sys.subject_tmpl or "Dispatch Alert"
            body_tmpl = tr.body_tmpl or self.sys.body_tmpl or "{trigger_list}"

            title = clamp(expand_template(title_tmpl, ctx_trg) or "Dispatch Alert", 250)
            body_raw = expand_template(body_tmpl, ctx_trg) or ""
            message, html_flag = sanitize_for_pushover(body_raw)

            payload = self._base_payload(
                app_token=tr.app_token,
                group_token=tr.group_token,
                title=title,
                message=message,
                sound=(tr.sound or self.sys.sound),
                html=1 if html_flag else 0,
                ctx=ctx_trg,
            )
            if self._post_with_retry(payload, timeout_s=timeout_s):
                sent += 1

        if sent:
            self.log.info("Pushover: sent %d per-trigger notification(s)", sent)
        else:
            self.log.debug("Pushover: no per-trigger notifications sent")
        return sent

    # --------- Internals ---------

    def _base_payload(self, *, app_token: str, group_token: str,
                      title: str, message: str, sound: Optional[str],
                      html: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the common Pushover form payload.
        Note: Pushover supports one clickable URL field; we prefer {audio_url} if present.
        """
        payload: Dict[str, Any] = {
            "token": app_token,
            "user": group_token,
            "message": clamp(message, 1024),
            "title": title,
            "html": 1 if html else 0,
        }
        if sound:
            payload["sound"] = str(sound)

        # Provide a stable timestamp so the push shows the event time
        ts = ctx.get("timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            payload["timestamp"] = int(ts)

        # Prefer a single URL pointing to the dispatch audio if present
        url = ctx.get("audio_url") or ctx.get("mp3_url")
        if isinstance(url, str) and url:
            payload["url"] = url
            payload["url_title"] = "Dispatch Audio"

        return payload

    def _post_with_retry(self, payload: Dict[str, Any], *, timeout_s: int, max_retries: int = 2) -> bool:
        """
        POST form-data to Pushover with light retry handling.
        Success = HTTP 200 and {"status":1} in body.
        """
        attempts = 0
        last_err: Optional[Exception] = None

        while attempts <= max_retries:
            attempts += 1
            try:
                resp = requests.post(self.PO_URL, data=payload, timeout=timeout_s)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        ok = bool(data.get("status") == 1)
                        if not ok:
                            self.log.warning("Pushover: error %s", truncate_json(data))
                        return ok
                    except Exception:
                        self.log.warning("Pushover: non-JSON response: %s", (resp.text or "")[:400])
                        return False

                if resp.status_code == 429:
                    # Respect Pushover's X-Limit-Reset / Retry-After when available
                    ra = resp.headers.get("Retry-After")
                    try:
                        delay = float(ra) if ra is not None else 1.5
                    except Exception:
                        delay = 1.5
                    self.log.warning("Pushover: 429 rate-limited; retrying in %.2fs (attempt %d/%d)",
                                     delay, attempts, max_retries)
                    time.sleep(delay)
                    continue

                self.log.warning("Pushover: HTTP %s %s", resp.status_code, (resp.text or "")[:400])
                return False

            except Exception as e:
                last_err = e
                self.log.debug("Pushover: request error %r (attempt %d/%d)", e, attempts, max_retries)
                time.sleep(0.5)

        if last_err:
            self.log.warning("Pushover: exhausted retries: %r", last_err)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_TAGS = ("b", "i", "u", "a")

def sanitize_for_pushover(s: str) -> Tuple[str, bool]:
    """
    Return (message, html_flag). Pushover allows only <b>, <i>, <u>, <a href="">.
    We:
      • strip <script>/<style>
      • unwrap <font>, <br>→ newline
      • drop all other tags but keep text
      • keep allowed tags
    If result still contains allowed tags, we return html_flag=True so we set html=1.
    """
    if not s:
        return "", False

    msg = str(s)

    # Normalize common line breaks
    msg = re.sub(r"(?i)<br\s*/?>", "\n", msg)

    # Strip script/style blocks
    msg = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", msg)

    # Unwrap font tags
    msg = re.sub(r"(?is)</?font[^>]*>", "", msg)

    # Remove any tag that is not allowed
    # Keep allowed tags with attributes (permit href on <a>)
    def _strip_disallowed(m: re.Match) -> str:
        tag = m.group(1).lower()
        if tag in _ALLOWED_TAGS:
            # Keep as-is
            return m.group(0)
        return ""  # drop the tag, keep inner text via separate pattern below if any

    # First, remove closing tags of disallowed types
    msg = re.sub(r"</(?!b|i|u|a)[^>]+>", "", msg)
    # Then remove opening tags of disallowed types
    msg = re.sub(r"<(?!b\b|i\b|u\b|a\b)([^>\s/]+)(?:\s[^>]*)?>", _strip_disallowed, msg)

    msg = msg.strip()

    # Decide whether to send html=1
    html_flag = bool(re.search(r"</?(?:b|i|u|a)\b", msg))

    # If no allowed tags left but still looks HTML-ish, fall back to plain text
    if not html_flag and ("<" in msg and ">" in msg):
        msg = html_to_text(msg)
        html_flag = False

    return msg, html_flag


def clamp(s: str, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= max_len else (s[: max_len - 1] + "…")


def truncate_json(data: Any, limit: int = 400) -> str:
    try:
        import json
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]
