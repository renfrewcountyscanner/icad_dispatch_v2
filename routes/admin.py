"""Admin routes for database management and imports."""
import sqlite3
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template, current_app
from .decorators import login_required, csrf_protect

bp_admin = Blueprint("admin", __name__)

# ───────────────────────────────────────────────────────────────
#  GET /admin
# ───────────────────────────────────────────────────────────────
@bp_admin.route("", methods=["GET"])
@login_required
def admin_home():
    """Admin dashboard."""
    return render_template("admin/index.html")


# ───────────────────────────────────────────────────────────────
#  POST /admin/import-db
# ───────────────────────────────────────────────────────────────
@bp_admin.route("/import-db", methods=["POST"])
@csrf_protect
@login_required
def import_db():
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
                        INSERT OR IGNORE INTO call_tone_events
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
                        INSERT OR IGNORE INTO call_transcripts
                        (call_id, transcript_text)
                        VALUES (?, ?)
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
                        INSERT OR IGNORE INTO call_vad_segments
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
                        INSERT OR IGNORE INTO trigger_fires
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
