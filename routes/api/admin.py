# routes/api/admin.py
# Admin API routes for user management
from flask import Blueprint, request, jsonify, current_app

from lib.user_module import (
    get_users,
    create_user,
    update_user,
    delete_user,
    set_user_password,
    get_user_systems,
    add_user_system,
    update_user_system,
    delete_user_system,
    get_all_user_systems_with_names,
)
from lib.system_module import get_systems
from routes.decorators import admin_required, csrf_protect

bp_admin_api = Blueprint("api_admin", __name__)


@bp_admin_api.route("/users", methods=["GET"])
@admin_required
def list_users():
    """List all users with their system counts."""
    db = current_app.config["db"]
    users = get_users(db)
    if users is None:
        return jsonify(success=False, message="Failed to fetch users"), 500

    result = []
    for u in users:
        systems = get_user_systems(db, u["user_id"])
        result.append({
            "user_id": u["user_id"],
            "username": u["user_username"],
            "is_admin": u.get("is_admin", 0),
            "is_active": u.get("is_active", 1),
            "systems_count": len(systems),
            "systems": systems,
        })

    return jsonify(success=True, result=result), 200


@bp_admin_api.route("/users", methods=["POST"])
@admin_required
@csrf_protect
def create_new_user():
    """Create a new user."""
    db = current_app.config["db"]
    data = request.get_json() or {}

    username = data.get("username", "").strip()
    password = data.get("password", "")
    is_admin = data.get("is_admin", False)

    if not username or not password:
        return jsonify(success=False, message="Username and password required"), 400

    if len(password) < 4:
        return jsonify(success=False, message="Password must be at least 4 characters"), 400

    res = create_user(db, username, password, is_admin=is_admin)
    if not res.get("success"):
        return jsonify(success=False, message=res.get("message", "Failed to create user")), 400

    return jsonify(success=True, message="User created successfully"), 201


@bp_admin_api.route("/users/<int:user_id>", methods=["PATCH"])
@admin_required
@csrf_protect
def update_existing_user(user_id):
    """Update user fields."""
    db = current_app.config["db"]
    data = request.get_json() or {}

    updates = {}
    if "username" in data:
        updates["user_username"] = data["username"].strip()
    if "is_admin" in data:
        updates["is_admin"] = 1 if data["is_admin"] else 0
    if "is_active" in data:
        updates["is_active"] = 1 if data["is_active"] else 0

    if not updates:
        return jsonify(success=False, message="No valid fields to update"), 400

    res = update_user(db, user_id, **updates)
    if not res.get("success"):
        return jsonify(success=False, message=res.get("message", "Failed to update user")), 400

    return jsonify(success=True, message="User updated successfully"), 200


@bp_admin_api.route("/users/<int:user_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def remove_user(user_id):
    """Delete a user."""
    if user_id == 1:
        return jsonify(success=False, message="Cannot delete root user"), 400

    db = current_app.config["db"]
    res = delete_user(db, user_id)
    if not res.get("success"):
        return jsonify(success=False, message=res.get("message", "Failed to delete user")), 400

    return jsonify(success=True, message="User deleted successfully"), 200


@bp_admin_api.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
@csrf_protect
def reset_user_password(user_id):
    """Reset a user's password (admin only)."""
    db = current_app.config["db"]
    data = request.get_json() or {}
    new_password = data.get("password", "")

    if not new_password or len(new_password) < 4:
        return jsonify(success=False, message="Password must be at least 4 characters"), 400

    res = set_user_password(db, user_id, new_password)
    if not res.get("success"):
        return jsonify(success=False, message="Failed to reset password"), 400

    return jsonify(success=True, message="Password reset successfully"), 200


@bp_admin_api.route("/users/<int:user_id>/systems", methods=["GET"])
@admin_required
def get_user_systems_list(user_id):
    """Get a user's system assignments."""
    db = current_app.config["db"]
    systems = get_user_systems(db, user_id)
    return jsonify(success=True, result=systems), 200


@bp_admin_api.route("/users/<int:user_id>/systems", methods=["PUT"])
@admin_required
@csrf_protect
def set_user_systems(user_id):
    """Set user's systems and permissions (replaces all existing)."""
    db = current_app.config["db"]
    data = request.get_json() or {}
    systems_list = data.get("systems", [])  # [{"radio_system_id": 1, "permission": "write"}, ...]

    # Delete existing assignments
    db.execute_commit("DELETE FROM user_systems WHERE user_id = ?", (user_id,))

    # Add new assignments
    for sys in systems_list:
        rsid = sys.get("radio_system_id")
        perm = sys.get("permission", "read")
        if rsid and perm in ("read", "write"):
            add_user_system(db, user_id, rsid, perm)

    return jsonify(success=True, message="User systems updated"), 200


@bp_admin_api.route("/users/<int:user_id>/systems/<int:radio_system_id>", methods=["PUT"])
@admin_required
@csrf_protect
def add_user_to_system(user_id, radio_system_id):
    """Add or update a user's permission for a specific system."""
    db = current_app.config["db"]
    data = request.get_json() or {}
    permission = data.get("permission", "read")

    if permission not in ("read", "write"):
        return jsonify(success=False, message="Invalid permission level"), 400

    # Check if exists
    existing = get_user_permission(db, user_id, radio_system_id)
    if existing:
        # Get user_system_id
        res = db.execute_query(
            "SELECT user_system_id FROM user_systems WHERE user_id = ? AND radio_system_id = ?",
            (user_id, radio_system_id),
            fetch_mode="one"
        )
        if res.get("success") and res.get("result"):
            update_user_system(db, res["result"]["user_system_id"], permission)
    else:
        add_user_system(db, user_id, radio_system_id, permission)

    return jsonify(success=True, message="System permission updated"), 200


@bp_admin_api.route("/users/<int:user_id>/systems/<int:radio_system_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def remove_user_from_system(user_id, radio_system_id):
    """Remove a system from a user."""
    db = current_app.config["db"]

    res = db.execute_query(
        "SELECT user_system_id FROM user_systems WHERE user_id = ? AND radio_system_id = ?",
        (user_id, radio_system_id),
        fetch_mode="one"
    )

    if res.get("success") and res.get("result"):
        delete_user_system(db, res["result"]["user_system_id"])

    return jsonify(success=True, message="System removed from user"), 200


@bp_admin_api.route("/systems", methods=["GET"])
@admin_required
def list_systems_for_admin():
    """List all systems for admin UI."""
    db = current_app.config["db"]
    res = get_systems(db)
    return jsonify(success=True, result=res.get("result", [])), 200