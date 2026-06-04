# lib/make_module.py
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from lib.dispatch_text_render import expand_template

module_logger = logging.getLogger("icad_dispatch.make_module")


# ─────────────────────────────────────────────────────────────────────────────
# Datamodels (mirror your system_row["make"] shape seen in logs)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MakeField:
    payload_field_id: Optional[int]
    field_key: str
    field_value_tmpl: str
    field_enabled: bool
    sort_order: int


@dataclass
class MakeSettings:
    enabled: bool
    webhook_url: Optional[str]
    api_key: Optional[str]
    fields: List[MakeField] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Sender
# ─────────────────────────────────────────────────────────────────────────────

class MakeSender:
    """
    Posts a JSON payload to a Make.com webhook endpoint.

    • Payload keys come from DB-configured `fields.field_key`
    • Each value is expanded from `fields.field_value` using the shared ctx (build_context)
    • Optional extras: add `timestamp` (epoch) and `timestamp_utc` (ISO) if present in ctx
    • Headers: `Content-Type: application/json`, `x-make-apikey: <api_key>`
    """
    def __init__(self, settings: MakeSettings, *, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.log = logger or module_logger

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.webhook_url)

    # Factory from system_row
    @staticmethod
    def from_system_row(system_row: Dict[str, Any],
                        *, logger: Optional[logging.Logger] = None) -> "MakeSender":
        m = (system_row or {}).get("make") or {}

        raw_fields = m.get("fields") or []
        fields: List[MakeField] = []
        for f in raw_fields:
            try:
                fields.append(
                    MakeField(
                        payload_field_id=f.get("payload_field_id"),
                        field_key=str(f.get("field_key") or "").strip(),
                        field_value_tmpl=str(f.get("field_value") or "").strip(),
                        field_enabled=bool(int(f.get("field_enabled"))) if isinstance(f.get("field_enabled"), (int, str)) else bool(f.get("field_enabled")),
                        sort_order=int(f.get("sort_order") or 0),
                    )
                )
            except Exception as e:
                (logger or module_logger).warning("MakeSender: skipping bad field row %s: %s", f, e)

        settings = MakeSettings(
            enabled=bool(int(m.get("enabled") or 0)),
            webhook_url=(m.get("webhook_url") or m.get("make_webhook_url")),
            api_key=(m.get("api_key") or m.get("make_webhook_api_key")),
            fields=fields,
        )
        return MakeSender(settings, logger=logger)

    # Public API
    def send(self, ctx: Dict[str, Any], *, timeout_s: int = 12, max_retries: int = 2) -> bool:
        if not self.enabled:
            self.log.debug("MakeSender: disabled or missing webhook_url; skipping")
            return False

        payload = self._build_payload(ctx)
        if not payload:
            self.log.debug("MakeSender: empty payload after expansion; skipping")
            return False

        headers = {
            "Content-Type": "application/json",
        }
        if self.settings.api_key:
            headers["x-make-apikey"] = str(self.settings.api_key)

        url = self.settings.webhook_url
        attempts = 0
        last_err: Optional[Exception] = None

        while attempts <= max_retries:
            attempts += 1
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout_s)
                if 200 <= resp.status_code < 300:
                    return True
                if resp.status_code == 429:
                    delay = _retry_after_seconds(resp)
                    self.log.warning("MakeSender: 429 rate-limited; retrying in %.2fs (attempt %d/%d)",
                                     delay, attempts, max_retries)
                    time.sleep(delay); continue
                self.log.warning("MakeSender: HTTP %s %s", resp.status_code, (resp.text or "")[:400])
                return False
            except Exception as e:
                last_err = e
                self.log.debug("MakeSender: request error %r (attempt %d/%d)", e, attempts, max_retries)
                time.sleep(0.5)

        if last_err:
            self.log.warning("MakeSender: exhausted retries: %r", last_err)
        return False

    # ───────────────────────── internals ─────────────────────────

    def _build_payload(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Expand DB-configured fields into a flat JSON dict.
        Skips disabled fields and keys that expand to empty string.
        Adds timestamp helpers if present in ctx.
        """
        # Expand fields (respect enabled + sort order)
        out: Dict[str, Any] = {}
        for f in sorted(self.settings.fields, key=lambda x: (x.sort_order, x.payload_field_id or 0)):
            if not f.field_enabled:
                continue
            key = f.field_key
            if not key:
                continue
            val = expand_template(f.field_value_tmpl or "", ctx) if f.field_value_tmpl else ""
            if val is None:
                continue
            val_str = str(val).strip()
            if val_str == "":
                # omit empty values to keep payload tidy
                continue
            out[key] = val_str

        # Helpful common fields if present in ctx
        ts_epoch = ctx.get("timestamp")
        if isinstance(ts_epoch, (int, float)) and ts_epoch > 0:
            out.setdefault("timestamp", int(ts_epoch))
        ts_iso = ctx.get("timestamp_utc")
        if isinstance(ts_iso, str) and ts_iso:
            out.setdefault("timestamp_utc", ts_iso)

        # Also include system identifiers if not already present
        if "system_id" in ctx:
            out.setdefault("system_id", ctx["system_id"])
        if "system_name" in ctx:
            out.setdefault("system_name", ctx["system_name"])

        # Shared map image URL
        map_url = ctx.get("map_image_url")
        if map_url:
            out.setdefault("map_image_url", map_url)

        return out


def _retry_after_seconds(resp: requests.Response) -> float:
    # Prefer Retry-After header if present; default to a short backoff
    ra = resp.headers.get("Retry-After")
    try:
        if ra is not None:
            val = float(ra)
            return val / 1000.0 if val > 30_000 else val
    except Exception:
        pass
    return 1.5
