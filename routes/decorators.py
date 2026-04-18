import hmac
from functools import wraps
from typing import Optional

from flask import redirect, url_for, session, request, jsonify, current_app, render_template, flash, abort, g


# ---------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------

def _extract_csrf_token() -> Optional[str]:
    """
    Best-effort extraction of a CSRF token from the incoming request.

    Accepts token from:
      - form body ('_csrf_token')
      - headers ('X-CSRFToken' / 'X-CSRF-Token')
      - JSON body ('_csrf_token' / 'csrf_token')
      - query string ('_csrf_token')
    """
    token = request.form.get("_csrf_token")
    if token:
        return token

    token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
    if token:
        return token

    if request.is_json:
        data = request.get_json(silent=True) or {}
        token = data.get("_csrf_token") or data.get("csrf_token")
        if token:
            return token

    token = request.args.get("_csrf_token")
    if token:
        return token

    return None


def csrf_protect(func):
    """
    CSRF protection decorator for form posts and JSON/XHR.

    Validates on unsafe methods (POST/PUT/PATCH/DELETE). If invalid:
      - For API/JSON requests, returns 403 JSON.
      - For browser flows, flashes an error and redirects back.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        if request.method in ("POST","PUT","PATCH","DELETE"):
            session_token = session.get("_csrf_token")
            sent_token    = _extract_csrf_token()
            ok = (session_token and sent_token and
                  hmac.compare_digest(str(session_token), str(sent_token)))
            if not ok:

                current_app.config["logger"].debug(
                    "CSRF fail: session_token=%s sent_token=%s cookie=%s path=%s",
                    'present' if session_token else 'missing',
                    'present' if sent_token else 'missing',
                    bool(request.cookies),
                    request.path,
                )
                wants_json = (
                        request.path.startswith("/api/")
                        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                        or "application/json" in (request.headers.get("Accept",""))
                )
                msg = "CSRF token missing or invalid."
                return (jsonify(success=False, message=msg), 403) if wants_json \
                    else redirect(request.referrer or "/")
        return func(*args, **kwargs)
    return wrapper

def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        authenticated = session.get('authenticated')
        if not authenticated:
            current_app.config['logger'].debug(f"Redirecting User: Current session data: {dict(session)}")
            return redirect(url_for('base_site.base_site_index'))
        else:
            current_app.config['logger'].debug(f"User Authorized: Current session data: {dict(session)}")

        return func(*args, **kwargs)

    return wrapper

def api_error(status: int, message: str, *, code: str | None = None, extra: dict | None = None):
    payload = {
        "success": False,
        "error": {
            "status": status,
            "message": message,
        }
    }
    if code:
        payload["error"]["code"] = code
    if extra:
        payload["error"].update(extra)
    return jsonify(payload), status

def token_required(fn):
    """
    Enforce system-scoped API-token authentication.

    • Token sources
        1.  HTTP  Authorization: Bearer <token>
        2.  form / query field  key=<token>

    • System identifier sources   (all map to *system_decimal*)
        - form / query field  system=<id>  | system_id=<id>
        - HTTP header         X-System-ID: <id>

    The decorator:
        1.  Retrieves   api_key & PK for the given system_decimal
        2.  Verifies    the supplied token matches
        3.  Injects     g.radio_system_id   (PK)
                        g.system_decimal    (public id)
                        g.api_token
                        g.radio_system      (entire row dict)
    """

    # ──────────────────────────────────────────────────────────────
    # helpers
    # ──────────────────────────────────────────────────────────────
    def _extract_token() -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.form.get("key") or request.values.get("key")

    def _extract_system_decimal() -> int | None:
        sid = (
                request.form.get("system")         or
                request.values.get("system")       or
                request.values.get("system_id")    or
                request.headers.get("X-System-ID")
        )
        try:
            return int(sid) if sid is not None else None
        except (TypeError, ValueError):
            return None

    # ──────────────────────────────────────────────────────────────
    # wrapper
    # ──────────────────────────────────────────────────────────────
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token      = _extract_token()
        sys_dec_id = _extract_system_decimal()

        if not token:
            return api_error(
                401,
                "Missing API token (Authorization header or key field).",
                code="missing_token",
                extra={"hint": "Send Authorization: Bearer <token> or form field key=<token>."},
            )

        if sys_dec_id is None:
            return api_error(
                400,
                "Missing or invalid system id (system / system_id / X-System-ID).",
                code="missing_system_id",
                extra={"hint": "Send system=<decimal> (form/query) or X-System-ID header."},
            )

        db = current_app.config["db"]
        row = db.execute_query(
            "SELECT radio_system_id, system_decimal, api_key "
            "FROM   radio_systems "
            "WHERE  system_decimal = ?",
            (sys_dec_id,),
            fetch_mode="one"
        )

        if not row.get("success"):
            return api_error(500, row.get("message") or "Database error.", code="db_error")

        if not row.get("result"):
            # You can keep 404 if you want “don’t leak existence”, but now it’s JSON.
            return api_error(404, "System not found.", code="system_not_found", extra={"system_decimal": sys_dec_id})

        if token != row["result"]["api_key"]:
            return api_error(403, "Invalid token for this system.", code="invalid_token", extra={"system_decimal": sys_dec_id})

        # ── expose for downstream handlers ───────────────────────
        g.radio_system_id = row["result"]["radio_system_id"]   # internal PK
        g.system_decimal  = row["result"]["system_decimal"]    # public id
        g.api_token       = token
        g.radio_system    = row["result"]

        return fn(*args, **kwargs)

    return wrapper


def token_or_login_required(fn):
    """
    Hybrid authentication: accept either API token OR session login.
    Useful for endpoints like upload that work with both SDRTrunk (tokens) and web UI (sessions).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Try token authentication first
        token = request.form.get("key") or request.values.get("key")
        if token and request.headers.get("Authorization", "").lower().startswith("bearer "):
            token = request.headers.get("Authorization")[7:].strip()

        sys_dec_id = (
            request.form.get("system") or
            request.values.get("system") or
            request.values.get("system_id") or
            request.headers.get("X-System-ID")
        )

        if token and sys_dec_id:
            # Try token authentication
            try:
                sys_dec_id = int(sys_dec_id)
            except (TypeError, ValueError):
                sys_dec_id = None

            if sys_dec_id:
                db = current_app.config["db"]
                row = db.execute_query(
                    "SELECT radio_system_id, system_decimal, api_key FROM radio_systems WHERE system_decimal = ?",
                    (sys_dec_id,),
                    fetch_mode="one"
                )

                if row.get("success") and row.get("result") and token == row["result"]["api_key"]:
                    # Token authentication successful
                    g.radio_system_id = row["result"]["radio_system_id"]
                    g.system_decimal = row["result"]["system_decimal"]
                    g.api_token = token
                    g.radio_system = row["result"]
                    return fn(*args, **kwargs)

        # Fall back to session authentication
        if "user_id" in session:
            # User is logged in, use first system (or default)
            db = current_app.config["db"]
            systems = db.execute_query("SELECT radio_system_id, system_decimal FROM radio_systems LIMIT 1", fetch_mode="one")
            if systems.get("success") and systems.get("result"):
                g.radio_system_id = systems["result"]["radio_system_id"]
                g.system_decimal = systems["result"]["system_decimal"]
                g.api_token = None
                return fn(*args, **kwargs)
            return api_error(400, "No systems configured.", code="no_systems")

        return api_error(401, "Missing authentication (API token or login session).", code="auth_required")

    return wrapper
