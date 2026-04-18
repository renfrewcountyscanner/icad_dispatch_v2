import logging
from typing import Any, Dict, List, Optional

import bcrypt
from flask import session

module_logger = logging.getLogger("icad_dispatch.user_handler")


# =============================================================================
# Public API
# =============================================================================
def get_users(db, user_id: Optional[int] = None, username: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch users filtered by `user_id` and/or `username`.

    Parameters
    ----------
    db : SQLiteDatabase
        Your database wrapper.
    user_id : int | None
        Filter by primary key.
    username : str | None
        Filter by exact username.

    Returns
    -------
    list[dict] | None
        List of row dicts if any match; otherwise None (preserves your prior behavior).
    """
    base = """
           SELECT
               ur.*
           FROM users ur \
           """

    where, params = [], []
    if user_id is not None:
        where.append("ur.user_id = ?")
        params.append(user_id)
    if username is not None:
        where.append("ur.user_username = ?")
        params.append(username)

    sql = base
    if where:
        sql += " WHERE " + " AND ".join(where)
    # GROUP BY is unnecessary for a single table; leaving out to avoid extra work.

    users_result = db.execute_query(sql, tuple(params) if params else None, fetch_mode="all")
    module_logger.debug("User Result: %s", users_result)

    if not users_result.get("success"):
        module_logger.error("get_users query failed: %s", users_result.get("message"))
        return None

    rows = users_result.get("result") or []
    return rows or None


def password_validate(database_password: Any, given_password: str) -> bool:
    """
    Validate a plaintext password against a stored bcrypt hash.

    Handles SQLite return types that might be `bytes`, `str`, or `memoryview`.

    Parameters
    ----------
    database_password : Any
        Stored bcrypt hash (ideally bytes). If str, it will be encoded as UTF-8.
    given_password : str
        Plaintext password from the user.

    Returns
    -------
    bool
        True if the password matches; False otherwise.
    """
    if database_password is None:
        return False

    # Normalize to bytes for bcrypt
    if isinstance(database_password, memoryview):
        database_password = database_password.tobytes()
    elif isinstance(database_password, str):
        database_password = database_password.encode("utf-8")

    try:
        return bcrypt.checkpw(given_password.encode("utf-8"), database_password)
    except Exception as e:
        module_logger.error("bcrypt check failed: %s", e)
        return False


def authenticate_user(db, username: str, password: str) -> Dict[str, Any]:
    """
    Authenticate a user by username and password, setting Flask session keys on success.

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str,
          "result": user_row (on success)
        }
    """
    users = get_users(db, username=username)
    if users is None or not users:
        return {"success": False, "message": "User not found."}

    user_row = users[0]
    if not password_validate(user_row.get("user_password"), password):
        module_logger.warning("Password Incorrect: %s", username)
        return {"success": False, "message": "Invalid Username or Password"}

    if not set_session_keys(user_row):
        module_logger.error("Cannot set session values for logged in user")
        return {"success": False, "message": "Internal Error"}

    return {"success": True, "message": "Authenticated Successfully", "result": user_row}


def set_session_keys(user_data: Dict[str, Any]) -> bool:
    """
    Store minimal authenticated user context in Flask session.

    Parameters
    ----------
    user_data : dict
        Row dict from `users`.

    Returns
    -------
    bool
        True on success; False otherwise.
    """
    module_logger.debug("Setting Session Keys from user_data=%s", user_data)
    try:
        username = user_data.get("user_username")
        user_id = user_data.get("user_id")
        if not username:
            raise ValueError("No Username in row")

        session.update(
            user_id=user_id,
            username=username,
            authenticated=True,
        )
        module_logger.debug("Session Keys set OK: %s", dict(session))
        return True

    except (IndexError, AttributeError, KeyError, ValueError) as e:
        module_logger.error("Session set failed: %s", e, exc_info=True)
        return False
    except Exception as e:
        module_logger.error("Unexpected error setting session: %s", e, exc_info=True)
        return False


def update_user_password(db, username: str, password: str) -> Dict[str, Any]:
    """
    Update a user's password (bcrypt hash) by username.

    Parameters
    ----------
    db : SQLiteDatabase
    username : str
    password : str

    Returns
    -------
    dict
        Standard `execute_commit` result. (Uses return_count=True for clarity.)
    """
    hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    q = "UPDATE users SET user_password = ? WHERE user_username = ?"
    return db.execute_commit(q, (hashed_password, username), return_row_id=False, return_count=True)


def user_change_password(db, username: str, current_password: str, new_password: str) -> Dict[str, Any]:
    """
    Change a user's password after verifying the current password.

    Parameters
    ----------
    db : SQLiteDatabase
    username : str
    current_password : str
    new_password : str

    Returns
    -------
    dict
        {
          "success": bool,
          "message": str
        }
    """
    users = get_users(db, username=username)
    if users is None or not users:
        return {"success": False, "message": "Username or password incorrect"}

    user_row = users[0]
    if not password_validate(user_row.get("user_password"), current_password):
        module_logger.warning("Password Incorrect for user '%s'", username)
        return {"success": False, "message": "Invalid Username or Password"}

    up = update_user_password(db, username, new_password)
    if up.get("success"):
        # Optionally, enforce that a row was affected:
        # if up.get("result", 0) == 0: return {"success": False, "message": "No password updated."}
        return {"success": True, "message": "Password Changed Successfully"}
    return {"success": False, "message": f"Password Change Failed. {up.get('message')}"}
