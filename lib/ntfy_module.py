# lib/ntfy_module.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from lib.dispatch_text_render import expand_template

module_logger = logging.getLogger("icad_dispatch.ntfy_module")


@dataclass
class NtfySettings:
    enabled: bool
    server_url: str
    topic: Optional[str]
    token: Optional[str]
    title_tmpl: str
    body_tmpl: str


class NtfySender:
    """Send push notifications via Ntfy (https://ntfy.sh or self-hosted)."""

    def __init__(self, settings: NtfySettings, *, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.log = logger or module_logger

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.server_url)

    @staticmethod
    def from_system_row(system_row: Dict[str, Any], *, logger: Optional[logging.Logger] = None) -> "NtfySender":
        ncfg = (system_row or {}).get("ntfy") or {}
        settings = NtfySettings(
            enabled=bool(int(ncfg.get("enabled") or 0)),
            server_url=(ncfg.get("server_url") or ncfg.get("ntfy_server_url") or "https://ntfy.sh"),
            topic=(ncfg.get("topic") or ncfg.get("ntfy_topic") or None),
            token=(ncfg.get("token") or ncfg.get("ntfy_token") or None),
            title_tmpl=(ncfg.get("title_tmpl") or ncfg.get("ntfy_title_tmpl") or "{system_name} • {trigger_list}"),
            body_tmpl=(ncfg.get("body_tmpl") or ncfg.get("ntfy_body_tmpl") or "{transcript}\n\n{audio_url}"),
        )
        return NtfySender(settings, logger=logger)

    def send(
        self,
        ctx: Dict[str, Any],
        *,
        fired_trigger_data: Optional[List[Dict[str, Any]]] = None,
        timeout_s: int = 12,
        max_retries: int = 2,
    ) -> int:
        """
        Send one notification per trigger that has alert_trigger_enable_ntfy=1.
        Returns the number of successful sends.
        """
        if not self.enabled:
            self.log.debug("NtfySender: disabled or missing server_url; skipping")
            return 0

        if not fired_trigger_data:
            self.log.debug("NtfySender: no fired triggers; skipping")
            return 0

        sent = 0
        for trig in fired_trigger_data:
            trig_enabled = bool(int(trig.get("alert_trigger_enable_ntfy") or 0))
            if not trig_enabled:
                continue

            # Per-trigger topic overrides system default
            trig_topic = (trig.get("alert_trigger_ntfy_topic") or "").strip()
            topic = trig_topic or (self.settings.topic or "").strip()
            if not topic:
                self.log.warning(
                    "NtfySender: trigger %s has no topic (system default also empty); skipping",
                    trig.get("alert_trigger_name", "?"),
                )
                continue

            # Build per-trigger context so {trigger_name} works in templates
            ctx_trg = dict(ctx)
            if "trigger_name" not in ctx_trg:
                ctx_trg["trigger_name"] = trig.get("alert_trigger_name") or ""

            title = expand_template(self.settings.title_tmpl, ctx_trg) or "Alert"
            body = expand_template(self.settings.body_tmpl, ctx_trg) or ""

            # Append shared map image URL if available
            map_url = ctx_trg.get("map_image_url")
            if map_url:
                body = body + f"\n\n📍 Map: {map_url}"

            url = f"{self.settings.server_url.rstrip('/')}/{requests.utils.quote(topic, safe='')}".rstrip("/")
            headers: Dict[str, str] = {
                "Title": title,
                "Priority": "3",
            }
            if self.settings.token:
                headers["Authorization"] = f"Bearer {self.settings.token}"

            attempt = 0
            last_err = None
            ok = False
            while attempt <= max_retries:
                attempt += 1
                try:
                    resp = requests.post(
                        url,
                        data=body.encode("utf-8"),
                        headers=headers,
                        timeout=timeout_s,
                    )
                    if 200 <= resp.status_code < 300:
                        ok = True
                        break
                    self.log.warning(
                        "NtfySender: HTTP %s for topic '%s': %s",
                        resp.status_code, topic, resp.text[:400],
                    )
                except Exception as e:
                    last_err = e
                    self.log.debug(
                        "NtfySender: request error for topic '%s' (attempt %d/%d): %r",
                        topic, attempt, max_retries + 1, e,
                    )

            if ok:
                sent += 1
                self.log.info("NtfySender: sent to topic '%s' (trigger '%s')", topic, trig.get("alert_trigger_name"))
            else:
                self.log.warning(
                    "NtfySender: failed to send to topic '%s' (trigger '%s') after %d attempts: %r",
                    topic, trig.get("alert_trigger_name"), max_retries + 1, last_err,
                )

        return sent
