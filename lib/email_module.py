# lib/email_module.py
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr, format_datetime, make_msgid
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Shared templating + text fallback
from lib.dispatch_text_render import expand_template, html_to_text

module_logger = logging.getLogger("icad_dispatch.email_module")


# ─────────────────────────────────────────────────────────────────────────────
# Data model pulled from system_row["email"]
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class EmailSettings:
    enabled: bool
    host: Optional[str]
    port: Optional[int]
    username: Optional[str]
    password: Optional[str]
    from_addr: str
    from_name: str
    subject_tmpl: str
    body_html_tmpl: str
    # Privacy: "bcc" (single send; hidden recipients) or "per_recipient" (one conn, N sends)
    privacy: str = "bcc"
    # What appears in the To: header for privacy="bcc"
    to_header: Optional[str] = None
    # SMTP envelope recipients (emails only)
    recipients: List[str] = field(default_factory=list)
    # Pretty header recipients (e.g., "Alice <a@x.com>") for per_recipient mode
    recipients_header: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Email sender (config-driven; no DB access)
# ─────────────────────────────────────────────────────────────────────────────
class EmailSender:
    def __init__(self, settings: EmailSettings, *, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.log = logger or module_logger

    @staticmethod
    def from_system_row(system_row: Dict[str, Any],
                        *, logger: Optional[logging.Logger] = None) -> "EmailSender":
        """Build an EmailSender directly from one system row (radio_system_config['result'][i])."""
        email_cfg = (system_row or {}).get("email") or {}

        raw_rcpts = email_cfg.get("recipients") or []
        # Normalize recipients into (emails list, header list)
        rcpt_emails, rcpt_headers = _extract_recipients(raw_rcpts)

        privacy = str(email_cfg.get("privacy") or "bcc").strip().lower()
        if privacy not in ("bcc", "per_recipient"):
            privacy = "bcc"

        to_header = email_cfg.get("to_header")
        if not to_header:
            # Default To: for BCC mode → show sender
            to_header = formataddr((
                (email_cfg.get("email_text_from") or "iCAD Dispatch").strip(),
                (email_cfg.get("email_address_from") or "dispatch@example.com").strip(),
            ))

        settings = EmailSettings(
            enabled=bool(int(email_cfg.get("enabled") or 0)),
            host=(email_cfg.get("smtp_hostname") or None),
            port=(int(email_cfg["smtp_port"]) if email_cfg.get("smtp_port") not in (None, "") else None),
            username=(email_cfg.get("smtp_username") or None),
            password=(email_cfg.get("smtp_password") or None),
            from_addr=(email_cfg.get("email_address_from") or "dispatch@example.com").strip(),
            from_name=(email_cfg.get("email_text_from") or "iCAD Dispatch").strip(),
            subject_tmpl=(email_cfg.get("email_alert_subject") or "Dispatch Alert"),
            body_html_tmpl=(email_cfg.get("email_alert_body") or "{trigger_list}"),
            recipients=rcpt_emails,
            recipients_header=rcpt_headers,
            privacy=privacy,
            to_header=to_header,
        )
        return EmailSender(settings, logger=logger)

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def send(
            self,
            ctx: Dict[str, Any],
            *,
            recipients: Optional[Iterable[str]] = None,   # optional override
            subject_override: Optional[str] = None,
            body_html_override: Optional[str] = None,
            timeout_s: int = 15,
    ) -> bool:
        """
        Send one HTML email (with text fallback) to all recipients.

        ctx is your canonical dispatch context (from lib.dispatch_text_render.build_context).
        """
        if not self.enabled:
            self.log.info("EmailSender: disabled; skipping send")
            return False

        if not self.settings.host or not self.settings.port:
            self.log.warning("EmailSender: SMTP host/port missing; skipping send")
            return False

        # Figure out recipients
        if recipients is not None:
            # Allow overrides as emails or "Name <email>" strings
            override_emails, override_headers = _extract_recipients(recipients)
            rcpts = _dedupe_emails(override_emails)
            email_to_header = _map_email_to_header(rcpts, override_headers)
        else:
            rcpts = _dedupe_emails(self.settings.recipients)
            email_to_header = _map_email_to_header(rcpts, self.settings.recipients_header)

        if not rcpts:
            self.log.info("EmailSender: no recipients; skipping send")
            return False

        # Back-compat alias
        ctx_eff = dict(ctx)
        if "transcript" not in ctx_eff and "transcript_text" in ctx_eff:
            ctx_eff["transcript"] = ctx_eff.get("transcript_text", "")

        subj_tmpl = subject_override or self.settings.subject_tmpl or "Dispatch Alert"
        body_tmpl = body_html_override or self.settings.body_html_tmpl or "{trigger_list}"

        # choose what you want "Date:" to represent:
        #   - event time (start of call), or
        #   - actual send time (now)
        event_epoch = None
        for k in ("start_epoch_s", "timestamp"):
            v = ctx_eff.get(k)
            if isinstance(v, (int, float)) and v:
                event_epoch = int(v); break
        send_dt = datetime.now(tz=timezone.utc).astimezone()
        date_dt = (datetime.fromtimestamp(event_epoch, tz=timezone.utc).astimezone()
                   if event_epoch else send_dt)

        subject = (expand_template(subj_tmpl, ctx_eff) or "Dispatch Alert")[:250]
        body_html = expand_template(body_tmpl, ctx_eff)

        map_image_url = ctx_eff.get("map_image_url")

        # Privacy modes
        if self.settings.privacy == "per_recipient":
            # One SMTP connection, multiple RCPT TO with individualized To: headers
            try:
                with self._smtp_connect(timeout_s) as smtp:
                    self._smtp_login_if_needed(smtp)
                    sent = 0
                    for rcpt in rcpts:
                        to_header = email_to_header.get(rcpt.strip().lower(), rcpt)
                        msg = self._build_message(
                            from_name=self.settings.from_name,
                            from_addr=self.settings.from_addr,
                            to_header=to_header,   # only THIS recipient shown
                            subject=subject,
                            html=body_html,
                            date_header=format_datetime(date_dt),
                            message_id=make_msgid(domain=(self.settings.from_addr.split("@",1)[-1].lower() or None)),
                            map_image_url=map_image_url,
                        )
                        smtp.sendmail(self.settings.from_addr, [rcpt], msg.as_string())
                        sent += 1
                self.log.info("EmailSender: sent privately to %d recipient(s)", sent)
                return True
            except Exception as e:
                self.log.warning(
                    "EmailSender: per-recipient send failed (host=%s port=%s): %s",
                    self.settings.host, self.settings.port, e
                )
                return False

        # Default: BCC-style single send (no Bcc header, envelope has all rcpts)
        try:
            msg = self._build_message(
                from_name=self.settings.from_name,
                from_addr=self.settings.from_addr,
                to_header=self.settings.to_header or self.settings.from_addr,  # nobody else shown
                subject=subject,
                html=body_html,
                date_header=format_datetime(date_dt),
                message_id=make_msgid(domain=(self.settings.from_addr.split("@",1)[-1].lower() or None)),
                map_image_url=map_image_url,
            )
            with self._smtp_connect(timeout_s) as smtp:
                self._smtp_login_if_needed(smtp)
                # Send once; all recipients are hidden (no Bcc header added to MIME)
                smtp.sendmail(self.settings.from_addr, rcpts, msg.as_string())
            self.log.info("EmailSender: sent (privacy=bcc) → %d recipient(s)", len(rcpts))
            return True
        except Exception as e:
            self.log.warning(
                "EmailSender: BCC send failed (host=%s port=%s): %s",
                self.settings.host, self.settings.port, e
            )
            return False

    # ─────────────────────────────────────────── internals ────────────────────
    @staticmethod
    def _build_message(*, from_name: str, from_addr: str,
                       to_header: str, subject: str, html: str,
                       date_header: str, message_id: str,
                       map_image_url: Optional[str] = None) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((from_name, from_addr))
        msg["To"] = to_header
        msg["Date"] = date_header          # ← required by RFC 5322
        msg["Message-ID"] = message_id     # ← strongly recommended
        msg["X-Mailer"] = "iCAD Dispatch"

        # Prepend map image to HTML if available
        if map_image_url:
            html = f'<p><img src="{map_image_url}" alt="Incident Map" style="max-width:100%;border-radius:8px;"></p>\n' + html

        text = html_to_text(html)
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg


    def _smtp_connect(self, timeout_s: int):
        """
        465 → implicit SSL
        587 → STARTTLS
        other → try STARTTLS if advertised, else plain
        """
        host, port = self.settings.host, int(self.settings.port)
        tls_ctx = ssl.create_default_context()

        if port == 465:
            self.log.debug("EmailSender: SSL %s:%s", host, port)
            smtp = smtplib.SMTP_SSL(host, port, timeout=timeout_s, context=tls_ctx)
            smtp.ehlo()
            return smtp

        self.log.debug("EmailSender: connect %s:%s", host, port)
        smtp = smtplib.SMTP(host, port, timeout=timeout_s)
        smtp.ehlo()
        if port == 587:
            self.log.debug("EmailSender: STARTTLS upgrade")
            smtp.starttls(context=tls_ctx); smtp.ehlo()
        else:
            try:
                if smtp.has_extn("starttls"):
                    self.log.debug("EmailSender: STARTTLS offered; upgrading")
                    smtp.starttls(context=tls_ctx); smtp.ehlo()
            except Exception as e:
                self.log.debug("EmailSender: STARTTLS not used: %s", e)
        return smtp

    def _smtp_login_if_needed(self, smtp: smtplib.SMTP) -> None:
        user, pw = self.settings.username, self.settings.password
        if user and pw:
            smtp.login(user, pw)


# ─────────────────────────────────────────────────────────────────────────────
# Utils
# ─────────────────────────────────────────────────────────────────────────────
def _extract_recipients(raw: Any) -> Tuple[List[str], List[str]]:
    """
    Accepts:
      • list[str] of emails or "Name <email>" strings
      • CSV/semicolon/newline string
      • list[dict] with keys like email/email_address/address + optional name/display_name/label
        and optional enabled/is_enabled/active flags
    Returns:
      (emails_for_envelope, pretty_headers)
    """
    emails: List[str] = []
    headers: List[str] = []

    tokens: List[Any]
    if isinstance(raw, str):
        tokens = re_split_delims(raw)
    elif isinstance(raw, Iterable):
        tokens = list(raw)
    else:
        tokens = []

    for tok in tokens:
        name, email, enabled = None, None, True

        if isinstance(tok, str):
            n, e = parseaddr(tok)
            name = n or None
            email = (e or tok).strip()
        elif isinstance(tok, dict):
            # enabled flags default to True if missing
            enabled_fields = [tok.get("enabled"), tok.get("is_enabled"), tok.get("active")]
            enabled = next((bool(int(v)) if isinstance(v, (int, str)) and str(v).isdigit() else bool(v)
                            for v in enabled_fields if v is not None), True)

            # email field variants
            email = (tok.get("email") or tok.get("email_address") or
                     tok.get("address") or tok.get("recipient") or "").strip()

            # name/display variants
            name = (tok.get("name") or tok.get("display_name") or tok.get("label") or "").strip() or None

            # allow dicts to store "Name <email>" in one field
            if not email:
                n, e = parseaddr(tok.get("to", "") or tok.get("value", ""))
                if e:
                    name, email = (n or None), e.strip()
        else:
            # unsupported token type
            continue

        if not enabled:
            continue
        if not email:
            continue

        emails.append(email)
        headers.append(formataddr((name or "", email)) if name else email)

    # De-dupe (case-insensitive by email), preserve order
    emails, headers = _dedupe_parallel(emails, headers)
    return emails, headers


def re_split_delims(s: str) -> List[str]:
    # split on comma/semicolon/newline
    import re
    return [p for p in re.split(r"[;,]\s*|\n+", s) if p and p.strip()]


def _dedupe_emails(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for it in items or []:
        e = (it or "").strip()
        key = e.lower()
        if not e or key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _dedupe_parallel(emails: List[str], headers: List[str]) -> Tuple[List[str], List[str]]:
    seen = set()
    out_e: List[str] = []
    out_h: List[str] = []
    for e, h in zip(emails, headers):
        key = (e or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out_e.append(e.strip())
        out_h.append(h.strip())
    return out_e, out_h


def _map_email_to_header(emails: List[str], headers: List[str]) -> Dict[str, str]:
    """
    Build a mapping {lower(email): header_string} so we can set a per-recipient To: when needed.
    """
    m: Dict[str, str] = {}
    for e, h in zip(emails, headers):
        key = (e or "").strip().lower()
        if key and key not in m:
            m[key] = h or e
    # Ensure every email has some header (fallback to itself)
    for e in emails:
        key = e.strip().lower()
        if key not in m:
            m[key] = e
    return {k: v for k, v in m.items()}
