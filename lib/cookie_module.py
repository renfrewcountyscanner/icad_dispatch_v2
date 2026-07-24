import os
import secrets, hashlib, hmac
from datetime import datetime, timedelta
from flask import current_app, session, request, g

_SERIES_LEN = 16   # bytes → 32-hex chars
_TOKEN_LEN  = 16
IS_DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")

def _hex(n): return secrets.token_hex(n)

def _hash(tok): return hashlib.sha256(tok.encode()).hexdigest()

# ---------- issue ----------
def issue_cookie(resp, db, user_id):
    series = _hex(_SERIES_LEN)
    token  = _hex(_TOKEN_LEN)
    db.execute_commit(
        "INSERT INTO user_remember_tokens (user_id, series, token_hash) VALUES (?,?,?)",
        (user_id, series, _hash(token))
    )
    _set_cookie(resp, f"{series}:{token}")

# ---------- verify + rotate ----------
def verify_cookie(db):
    raw = request.cookies.get("remember_me")
    if not raw or ":" not in raw: return None
    series, token = raw.split(":", 1)
    rows = db.execute_query(
        "SELECT id, user_id, token_hash FROM user_remember_tokens WHERE series=?",
        (series,)
    )["result"]
    if not rows: return None
    row = rows[0]
    if not hmac.compare_digest(row["token_hash"], _hash(token)):
        # token mismatch → probable theft: purge series
        db.execute_commit("DELETE FROM user_remember_tokens WHERE series=?", (series,))
        return None

    # success → rotate
    new_token = _hex(_TOKEN_LEN)
    db.execute_commit(
        "UPDATE user_remember_tokens SET token_hash=?, issued_at=CURRENT_TIMESTAMP WHERE id=?",
        (_hash(new_token), row["id"])
    )
    g._remember_outgoing_cookie = f"{series}:{new_token}"
    return row["user_id"]

def _set_cookie(resp, value):
    resp.set_cookie(
        "remember_me", value,
        max_age=60*60*24*30,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "true").lower() in ("1", "true", "yes", "on") and not IS_DEBUG,
        domain=os.getenv("SESSION_COOKIE_DOMAIN"),
        samesite="Lax",
        path="/"
    )
