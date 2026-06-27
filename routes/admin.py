"""Admin routes for database management and imports."""
import sqlite3
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template, current_app, session
from .decorators import login_required, csrf_protect, admin_required
from lib.user_module import (
    get_users,
    create_user,
    delete_user,
    get_user_systems,
    add_user_system,
    update_user,
    set_user_password,
    delete_user_system,
)
from lib.system_module import get_systems

bp_admin = Blueprint("admin", __name__)

# Minimum password length enforced for admin-managed accounts.
MIN_PASSWORD_LENGTH = 8


def _validate_password(password: str) -> str | None:
    """Return an error message if the password is too weak, else None."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number"
    if password.isalnum():
        return "Password must contain at least one symbol"
    return None

# ───────────────────────────────────────────────────────────────
#  GET /admin
# ───────────────────────────────────────────────────────────────
@bp_admin.route("", methods=["GET"])
@login_required
def admin_home():
    """Admin dashboard."""
    if not session.get("is_admin"):
        return render_template("base_site/index.html"), 403
    return render_template("admin/index.html")


# ───────────────────────────────────────────────────────────────
#  POST /admin/import-db
# ───────────────────────────────────────────────────────────────
@bp_admin.route("/import-db", methods=["POST"])
@csrf_protect
@login_required
def import_db():
    if not session.get("is_admin"):
        return jsonify(success=False, message="Admin access required"), 403
    """
    Import call records and related data from an uploaded SQLite database.
    Merges data by skipping duplicate call_ids.
    """
    db = current_app.config["db"]
    logger = current_app.config["logger"]

    if "db_file" not in request.files:
        return jsonify(success=False, message="No file uploaded"), 400

    file = request.files["db_file"]
    if not file or file.filename == "":
        return jsonify(success=False, message="No file selected"), 400

    if not file.filename.endswith(".db"):
        return jsonify(success=False, message="File must be a .db SQLite database"), 400

    temp_path = None
    try:
        # Save uploaded file temporarily
        temp_path = Path(f"/tmp/{file.filename}")
        file.save(str(temp_path))

        # Connect to uploaded database
        source_db = sqlite3.connect(str(temp_path))
        source_db.row_factory = sqlite3.Row
        source_cursor = source_db.cursor()

        logger.info("import-db: starting import from %s", file.filename)

        # Tables to import in order (respecting foreign key dependencies)
        stats = {
            "calls_imported": 0,
            "calls_skipped": 0,
            "tones_imported": 0,
            "transcripts_imported": 0,
            "vad_segments_imported": 0,
            "trigger_fires_imported": 0,
            "errors": 0,
        }

        # Get list of existing call_ids to skip duplicates
        existing_calls_res = db.execute_query(
            "SELECT call_id FROM call_records", fetch_mode="all"
        )
        existing_calls = (
            {r["call_id"] for r in (existing_calls_res["result"] or [])}
            if existing_calls_res["success"]
            else set()
        )
        logger.info("import-db: found %d existing calls", len(existing_calls))

        # Import call_records
        try:
            source_cursor.execute(
                """
                SELECT call_id, radio_system_id, talkgroup, talkgroup_name,
                       start_epoch_s, duration_s, audio_url, incident_category,
                       has_transcript, has_vad
                FROM call_records
                """
            )
            for row in source_cursor.fetchall():
                call_id = row["call_id"]
                if call_id in existing_calls:
                    stats["calls_skipped"] += 1
                    continue

                db.execute_commit(
                    """
                    INSERT INTO call_records
                    (call_id, radio_system_id, talkgroup, talkgroup_name,
                     start_epoch_s, duration_s, audio_url, incident_category,
                     has_transcript, has_vad)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["call_id"],
                        row["radio_system_id"],
                        row["talkgroup"],
                        row["talkgroup_name"],
                        row["start_epoch_s"],
                        row["duration_s"],
                        row["audio_url"],
                        row["incident_category"],
                        row["has_transcript"],
                        row["has_vad"],
                    ),
                )
                stats["calls_imported"] += 1
                existing_calls.add(call_id)
        except Exception as e:
            logger.error("import-db: error importing call_records: %s", e)
            stats["errors"] += 1

        # Import call_tone_events
        try:
            source_cursor.execute(
                """
                SELECT call_id, tone_type, json_payload, matches_trigger
                FROM call_tone_events
                WHERE call_id IN (SELECT call_id FROM call_records WHERE call_id IN (%s))
                """
                % ",".join("?" * len(existing_calls)),
                list(existing_calls),
            )
            for row in source_cursor.fetchall():
                try:
                    db.execute_commit(
                        """
                        INSERT INTO call_tone_events
                        (call_id, tone_type, json_payload, matches_trigger)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            row["call_id"],
                            row["tone_type"],
                            row["json_payload"],
                            row["matches_trigger"],
                        ),
                    )
                    stats["tones_imported"] += 1
                except Exception as e:
                    logger.error(
                        "import-db: error importing tone for call %s: %s",
                        row["call_id"],
                        e,
                    )
                    stats["errors"] += 1
        except Exception as e:
            logger.error("import-db: error importing call_tone_events: %s", e)
            stats["errors"] += 1

        # Import call_transcripts
        try:
            source_cursor.execute(
                """
                SELECT call_id, transcript_text
                FROM call_transcripts
                WHERE call_id IN (SELECT call_id FROM call_records WHERE call_id IN (%s))
                """
                % ",".join("?" * len(existing_calls)),
                list(existing_calls),
            )
            for row in source_cursor.fetchall():
                try:
                    db.execute_commit(
                        """
                        INSERT INTO call_transcripts
                        (call_id, transcript_text)
                        VALUES (?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        (row["call_id"], row["transcript_text"]),
                    )
                    stats["transcripts_imported"] += 1
                except Exception as e:
                    logger.error(
                        "import-db: error importing transcript for call %s: %s",
                        row["call_id"],
                        e,
                    )
                    stats["errors"] += 1
        except Exception as e:
            logger.error("import-db: error importing call_transcripts: %s", e)
            stats["errors"] += 1

        # Import call_vad_segments
        try:
            source_cursor.execute(
                """
                SELECT call_id, start_s, end_s
                FROM call_vad_segments
                WHERE call_id IN (SELECT call_id FROM call_records WHERE call_id IN (%s))
                """
                % ",".join("?" * len(existing_calls)),
                list(existing_calls),
            )
            for row in source_cursor.fetchall():
                try:
                    db.execute_commit(
                        """
                        INSERT INTO call_vad_segments
                        (call_id, start_s, end_s)
                        VALUES (?, ?, ?)
                        """,
                        (row["call_id"], row["start_s"], row["end_s"]),
                    )
                    stats["vad_segments_imported"] += 1
                except Exception as e:
                    logger.error(
                        "import-db: error importing VAD segment for call %s: %s",
                        row["call_id"],
                        e,
                    )
                    stats["errors"] += 1
        except Exception as e:
            logger.error("import-db: error importing call_vad_segments: %s", e)
            stats["errors"] += 1

        # Import trigger_fires
        try:
            source_cursor.execute(
                """
                SELECT call_id, alert_trigger_id, fired_at_epoch_s
                FROM trigger_fires
                WHERE call_id IN (SELECT call_id FROM call_records WHERE call_id IN (%s))
                """
                % ",".join("?" * len(existing_calls)),
                list(existing_calls),
            )
            for row in source_cursor.fetchall():
                try:
                    db.execute_commit(
                        """
                        INSERT INTO trigger_fires
                        (call_id, alert_trigger_id, fired_at_epoch_s)
                        VALUES (?, ?, ?)
                        """,
                        (
                            row["call_id"],
                            row["alert_trigger_id"],
                            row["fired_at_epoch_s"],
                        ),
                    )
                    stats["trigger_fires_imported"] += 1
                except Exception as e:
                    logger.error(
                        "import-db: error importing trigger_fire for call %s: %s",
                        row["call_id"],
                        e,
                    )
                    stats["errors"] += 1
        except Exception as e:
            logger.error("import-db: error importing trigger_fires: %s", e)
            stats["errors"] += 1

        source_db.close()
        logger.info("import-db: completed - %s", stats)

        return jsonify(success=True, result=stats)

    except Exception as e:
        logger.error("import-db: unexpected error: %s", e)
        return jsonify(success=False, message=f"Import failed: {str(e)}"), 500
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


# ───────────────────────────────────────────────────────────────
#  GET /admin/users
# ───────────────────────────────────────────────────────────────
@bp_admin.route("/users", methods=["GET"])
@login_required
def admin_users():
    """User management page - requires admin."""
    if not session.get("is_admin"):
        return render_template("base_site/index.html"), 403

    db = current_app.config["db"]

    # Get all users
    users = get_users(db) or []

    # Get systems for assignment
    systems_res = get_systems(db)
    systems = systems_res.get("result", []) if systems_res.get("success") else []

    # Get each user's assigned systems
    user_data = []
    for u in users:
        user_systems = get_user_systems(db, u["user_id"])
        user_data.append({
            "user_id": u["user_id"],
            "username": u["user_username"],
            "is_admin": u.get("is_admin", 0),
            "is_active": u.get("is_active", 1),
            "systems": user_systems,
        })

    return render_template("admin/users.html", users=user_data, systems=systems)


# ───────────────────────────────────────────────────────────────
#  POST /admin/users (create user)
# ───────────────────────────────────────────────────────────────
@bp_admin.route("/users", methods=["POST"])
@csrf_protect
@login_required
def admin_users_create():
    """Create a new user."""
    if not session.get("is_admin"):
        return jsonify(success=False, message="Admin required"), 403

    db = current_app.config["db"]

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    is_admin = request.form.get("is_admin") == "on"
    system_ids = request.form.getlist("systems")

    if not username or not password:
        return jsonify(success=False, message="Username and password required"), 400

    pw_error = _validate_password(password)
    if pw_error:
        return jsonify(success=False, message=pw_error), 400

    # Create user
    res = create_user(db, username, password, is_admin=is_admin)
    if not res.get("success"):
        return jsonify(success=False, message=res.get("message", "Failed to create user")), 400

    user_id = res.get("result")

    # Assign systems
    for sys_id in system_ids:
        perm = request.form.get(f"perm_{sys_id}", "read")
        add_user_system(db, user_id, int(sys_id), perm)

    return jsonify(success=True, message="User created successfully")


# ───────────────────────────────────────────────────────────────
#  PATCH /admin/users/<user_id> (edit user)
# ───────────────────────────────────────────────────────────────
@bp_admin.route("/users/<int:user_id>", methods=["PATCH"])
@csrf_protect
@login_required
def admin_users_update(user_id):
    """Update an existing user: username, admin/active flags, password, systems."""
    if not session.get("is_admin"):
        return jsonify(success=False, message="Admin required"), 403

    db = current_app.config["db"]

    existing = get_users(db, user_id=user_id)
    if not existing:
        return jsonify(success=False, message="User not found"), 404

    data = request.get_json(silent=True) or {}

    # Build the field updates (username / flags)
    updates = {}
    if "username" in data:
        new_username = str(data["username"]).strip()
        if not new_username:
            return jsonify(success=False, message="Username cannot be empty"), 400
        updates["user_username"] = new_username
    if "is_admin" in data:
        # Never allow the root user (id 1) to lose admin status.
        if user_id == 1 and not data["is_admin"]:
            return jsonify(success=False, message="Cannot remove admin from root user"), 400
        updates["is_admin"] = 1 if data["is_admin"] else 0
    if "is_active" in data:
        if user_id == 1 and not data["is_active"]:
            return jsonify(success=False, message="Cannot deactivate root user"), 400
        updates["is_active"] = 1 if data["is_active"] else 0

    if updates:
        res = update_user(db, user_id, **updates)
        if not res.get("success"):
            return jsonify(success=False, message=res.get("message", "Update failed")), 400

    # Optional password reset
    new_password = data.get("password")
    if new_password:
        pw_error = _validate_password(new_password)
        if pw_error:
            return jsonify(success=False, message=pw_error), 400
        pw_res = set_user_password(db, user_id, new_password)
        if not pw_res.get("success"):
            return jsonify(success=False, message="Failed to update password"), 400

    # Optional system reassignment (full replace if "systems" provided)
    if "systems" in data and isinstance(data["systems"], dict):
        current = get_user_systems(db, user_id)
        desired = {int(k): v for k, v in data["systems"].items()}

        # Remove assignments no longer wanted, then (re)add desired ones.
        all_assignments = db.execute_query(
            "SELECT user_system_id, radio_system_id FROM user_systems WHERE user_id = ?",
            (user_id,),
            fetch_mode="all",
        )
        rows = all_assignments.get("result") or [] if all_assignments.get("success") else []
        for row in rows:
            if row["radio_system_id"] not in desired:
                delete_user_system(db, row["user_system_id"])

        for sys_id, perm in desired.items():
            if perm not in ("read", "write"):
                perm = "read"
            if sys_id in current:
                # Update existing assignment's permission in place.
                db.execute_commit(
                    "UPDATE user_systems SET permission_level = ? WHERE user_id = ? AND radio_system_id = ?",
                    (perm, user_id, sys_id),
                )
            else:
                add_user_system(db, user_id, sys_id, perm)

    return jsonify(success=True, message="User updated successfully")


# ───────────────────────────────────────────────────────────────
#  POST /admin/users/<user_id>/delete
# ───────────────────────────────────────────────────────────────
@bp_admin.route("/users/<int:user_id>", methods=["DELETE"])
@csrf_protect
@login_required
def admin_users_delete(user_id):
    """Delete a user."""
    if not session.get("is_admin"):
        return jsonify(success=False, message="Admin required"), 403

    if user_id == 1:
        return jsonify(success=False, message="Cannot delete root user"), 400

    db = current_app.config["db"]
    res = delete_user(db, user_id)

    if not res.get("success"):
        return jsonify(success=False, message=res.get("message", "Failed to delete user")), 400

    return jsonify(success=True, message="User deleted successfully")
