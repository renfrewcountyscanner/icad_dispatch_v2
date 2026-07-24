# routes/middleware.py
import base64
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Dict, Any

from flask import request, session, current_app
from flask import g
from lib.cookie_module import verify_cookie, _set_cookie
from lib.user_module import get_users, set_session_keys

_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 8
_login_attempts = defaultdict(deque)
_login_lock = threading.Lock()

def login_rate_limited(key: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        attempts = _login_attempts[key]
        while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            return True
        attempts.append(now)
        return False

def clear_login_attempts(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)

def log_ip():
    """Logs the IP address of the incoming request."""
    ip_address = request.remote_addr
    #current_app.config['logger'].debug(f"Request received from IP address: {ip_address}")

def generate_csrf_token():
    """Generates a CSRF token if not already in the session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = base64.urlsafe_b64encode(os.urandom(24)).decode('utf-8')
    return session['_csrf_token']

def inject_csrf_token():
    """Injects the CSRF token into the context processor."""
    return dict(csrf_token=generate_csrf_token())

REDACT_KEYS = {
    "key", "api_key", "apikey", "token", "access_token", "authorization",
    "password", "passwd", "secret", "signature",
}

def _truncate(v: object, max_len: int = 500) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    return s if len(s) <= max_len else (s[:max_len] + f"...(+{len(s)-max_len} chars)")

def _sanitize_multidict(md, *, max_len: int = 500) -> dict:
    """
    Convert MultiDict -> dict
      - redacts sensitive keys
      - preserves multi-values as list
      - truncates long values
    """
    out = {}
    for k in md.keys():
        lk = k.lower()
        vals = md.getlist(k)

        if lk in REDACT_KEYS:
            out[k] = "***REDACTED***" if len(vals) == 1 else ["***REDACTED***"] * len(vals)
            continue

        cleaned = [_truncate(v, max_len=max_len) for v in vals]
        out[k] = cleaned[0] if len(cleaned) == 1 else cleaned
    return out

def _sanitize_json(obj, *, max_len: int = 500):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in REDACT_KEYS:
                out[k] = "***REDACTED***"
            else:
                out[k] = _sanitize_json(v, max_len=max_len)
        return out
    if isinstance(obj, list):
        return [_sanitize_json(x, max_len=max_len) for x in obj]
    # primitives
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return _truncate(obj, max_len=max_len)
    return _truncate(obj, max_len=max_len)

def log_request_path():
    """
    ONE debug log line per request.

    - Non-POST: summary only
    - POST: summary + args/form/files/json (sanitized)
    """
    log = current_app.config.get("logger") or current_app.logger

    method = request.method
    path = request.path
    ct = request.content_type
    ln = request.content_length
    remote = request.headers.get("X-Forwarded-For", request.remote_addr)

    if method != "POST":
        log.debug("Request %s %s ip=%s ct=%s len=%s", method, path, remote, ct, ln)
        return

    # POST extras
    args = _sanitize_multidict(request.args)
    form = _sanitize_multidict(request.form)

    files = []
    for field in request.files:
        for fs in request.files.getlist(field):
            files.append({
                "field": field,
                "filename": fs.filename,
                "content_type": fs.mimetype,
                "size": fs.content_length,  # may be None depending on server
            })

    json_body = request.get_json(silent=True)
    json_body = _sanitize_json(json_body) if json_body is not None else None

    log.debug(
        "Request %s %s ip=%s ct=%s len=%s args=%s form=%s files=%s json=%s",
        method, path, remote, ct, ln, args, form, files, json_body
    )

def load_remembered_user():
    if session.get("authenticated"):      # still in normal session
        return
    user_id = verify_cookie(current_app.config["db"])
    if user_id:
        # Rebuild your session quickly
        user_row = get_users(current_app.config['db'], user_id=user_id)
        if user_row:
            set_session_keys(current_app.config['db'], user_row[0])

def attach_rotated_cookie(resp):
    if hasattr(g, "_remember_outgoing_cookie"):
        _set_cookie(resp, g._remember_outgoing_cookie)
    return resp

def add_security_headers(resp):
    """Apply conservative browser protections to HTML and API responses."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.path == "/login" or request.path.startswith(("/auth/", "/dashboard", "/admin", "/api/")):
        resp.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure and current_app.config.get("PUBLIC_DEPLOYMENT"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp
