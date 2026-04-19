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

    # Check if account is active
    if not user_row.get("is_active", 1):
        module_logger.warning("Inactive user attempted login: %s", username)
        return {"success": False, "message": "Account is disabled."}

    if not password_validate(user_row.get("user_password"), password):
        module_logger.warning("Password Incorrect: %s", username)
        return {"success": False, "message": "Invalid Username or Password"}

    if not set_session_keys(db, user_row):
        module_logger.error("Cannot set session values for logged in user")
        return {"success": False, "message": "Internal Error"}

    return {"success": True, "message": "Authenticated Successfully", "result": user_row}


def set_session_keys(db, user_data: Dict[str, Any]) -> bool:
    """
    Store authenticated user context in Flask session, including systems/permissions.

    Parameters
    ----------
    db : SQLiteDatabase
        Database connection (needed to fetch user systems).
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

        # Get user's systems and permissions
        user_systems = get_user_systems(db, user_id)
        is_admin = bool(user_data.get("is_admin", 0))

        session.update(
            user_id=user_id,
            username=username,
            is_admin=is_admin,
            user_systems=user_systems,
            authenticated=True,
        )
        module_logger.debug("Session Keys set OK: user_id=%s is_admin=%s systems=%s",
                           user_id, is_admin, list(user_systems.keys()))
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


# =============================================================================
# User Management (Admin Functions)
# =============================================================================
def is_user_admin(db, user_id: int) -> bool:
    """Check if user is admin."""
    res = db.execute_query(
        "SELECT is_admin FROM users WHERE user_id = ?",
        (user_id,),
        fetch_mode="one"
    )
    return res.get("success") and res.get("result", {}).get("is_admin", 0) == 1


def is_user_active(db, user_id: int) -> bool:
    """Check if user account is active."""
    res = db.execute_query(
        "SELECT is_active FROM users WHERE user_id = ?",
        (user_id,),
        fetch_mode="one"
    )
    return res.get("success") and res.get("result", {}).get("is_active", 0) == 1


def get_user_systems(db, user_id: int) -> Dict[int, str]:
    """
    Get all systems a user has access to.
    Returns dict: {radio_system_id: permission_level}
    """
    res = db.execute_query(
        """SELECT radio_system_id, permission_level
           FROM user_systems WHERE user_id = ?""",
        (user_id,),
        fetch_mode="all"
    )
    if not res.get("success"):
        module_logger.error("get_user_systems failed: %s", res.get("message"))
        return {}

    return {r["radio_system_id"]: r["permission_level"] for r in (res["result"] or [])}


def get_user_permission(db, user_id: int, radio_system_id: int) -> Optional[str]:
    """Get user's permission level for a specific system. Returns None if no access."""
    res = db.execute_query(
        """SELECT permission_level FROM user_systems
           WHERE user_id = ? AND radio_system_id = ?""",
        (user_id, radio_system_id),
        fetch_mode="one"
    )
    if not res.get("success") or not res.get("result"):
        return None
    return res["result"]["permission_level"]


def add_user_system(db, user_id: int, radio_system_id: int, permission_level: str = "read") -> Dict[str, Any]:
    """Assign a system to a user with given permission level."""
    if permission_level not in ("read", "write"):
        return {"success": False, "message": "Invalid permission_level"}

    res = db.execute_commit(
        """INSERT INTO user_systems (user_id, radio_system_id, permission_level)
           VALUES (?, ?, ?)""",
        (user_id, radio_system_id, permission_level)
    )
    return res


def update_user_system(db, user_system_id: int, permission_level: str) -> Dict[str, Any]:
    """Update permission level for a user-system assignment."""
    if permission_level not in ("read", "write"):
        return {"success": False, "message": "Invalid permission_level"}

    res = db.execute_commit(
        """UPDATE user_systems SET permission_level = ? WHERE user_system_id = ?""",
        (permission_level, user_system_id)
    )
    return res


def delete_user_system(db, user_system_id: int) -> Dict[str, Any]:
    """Remove a system from a user."""
    res = db.execute_commit(
        "DELETE FROM user_systems WHERE user_system_id = ?",
        (user_system_id,)
    )
    return res


def create_user(db, username: str, password: str, is_admin: bool = False) -> Dict[str, Any]:
    """Create a new user."""
    hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    res = db.execute_commit(
        """INSERT INTO users (user_username, user_password, is_admin, is_active)
           VALUES (?, ?, ?, 1)""",
        (username, hashed_password, 1 if is_admin else 0)
    )
    return res


def update_user(db, user_id: int, **kwargs) -> Dict[str, Any]:
    """Update user fields (username, is_admin, is_active)."""
    allowed = {"user_username", "is_admin", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if not updates:
        return {"success": False, "message": "No valid fields to update"}

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [user_id]

    res = db.execute_commit(
        f"UPDATE users SET {set_clause} WHERE user_id = ?",
        tuple(values)
    )
    return res


def delete_user(db, user_id: int) -> Dict[str, Any]:
    """Delete a user (cascade removes user_systems entries)."""
    if user_id == 1:
        return {"success": False, "message": "Cannot delete root user"}

    res = db.execute_commit(
        "DELETE FROM users WHERE user_id = ?",
        (user_id,)
    )
    return res


def set_user_password(db, user_id: int, password: str) -> Dict[str, Any]:
    """Set a user's password (admin can reset without knowing current)."""
    hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    res = db.execute_commit(
        "UPDATE users SET user_password = ? WHERE user_id = ?",
        (hashed_password, user_id)
    )
    return res


def get_all_user_systems_with_names(db) -> List[Dict[str, Any]]:
    """Get all user-system assignments with system names for admin UI."""
    res = db.execute_query(
        """SELECT us.user_system_id, us.user_id, us.radio_system_id,
                  us.permission_level, u.user_username, rs.system_name
           FROM user_systems us
           JOIN users u ON us.user_id = u.user_id
           JOIN radio_systems rs ON us.radio_system_id = rs.radio_system_id
           ORDER BY u.user_username, rs.system_name""",
        fetch_mode="all"
    )
    if not res.get("success"):
        return []
    return res["result"] or []
