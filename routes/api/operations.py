"""Dashboard operational health and safe enrichment retry endpoints."""

from __future__ import annotations

import json
import logging

from flask import Blueprint, current_app, jsonify, request

from lib.operations_status import get_operations_status
from routes.decorators import csrf_protect, login_required

bp_operations = Blueprint("api_operations", __name__)


@bp_operations.route("/status", methods=["GET"])
@login_required
def status():
    try:
        hours = int(request.args.get("hours", 24))
    except ValueError:
        return jsonify(success=False, message="hours must be an integer"), 400

    result = get_operations_status(current_app.config["db"], hours)
    return jsonify(result), 200 if result["success"] else 500


@bp_operations.route("/calls/<int:call_id>/retry-geocoding", methods=["POST"])
@login_required
@csrf_protect
def retry_geocoding(call_id: int):
    """Re-run address extraction/geocoding without reprocessing tones or alerts."""
    db = current_app.config["db"]
    logger = logging.getLogger("icad_dispatch.operations")
    call = db.execute_query(
        """
        SELECT cr.radio_system_id, ct.text_full
        FROM call_records cr
        JOIN call_transcripts ct ON ct.call_id = cr.call_id
        WHERE cr.call_id = ?
        """,
        (call_id,),
        fetch_mode="one",
    )
    row = call.get("result") if call.get("success") else None
    if not row:
        return jsonify(success=False, message="Call with a transcript was not found"), 404

    from routes.api.call_upload import _load_address_extraction_service

    service = _load_address_extraction_service(db, row["radio_system_id"], logger)
    if not service:
        return jsonify(success=False, message="Address extraction is not configured for this system"), 400

    try:
        result = service.extract_and_geocode((row.get("text_full") or "").strip())
    except Exception as exc:
        logger.warning("Geocode retry failed for call_id=%s: %s", call_id, exc)
        return jsonify(success=False, message="Address retry failed; check server logs"), 502

    extracted = result.get("extracted") if result else None
    geocoded = result.get("geocoded") if result else None
    update = db.execute_commit(
        """
        UPDATE call_transcripts
        SET address_extracted_json = ?, address_geocoded_json = ?
        WHERE call_id = ?
        """,
        (
            json.dumps(extracted.to_dict(), ensure_ascii=False) if extracted else None,
            json.dumps(geocoded.to_dict(), ensure_ascii=False) if geocoded else None,
            call_id,
        ),
    )
    if not update.get("success"):
        return jsonify(success=False, message="Could not save the retry result"), 500

    return jsonify(success=True, geocoded=bool(geocoded), message="Address extraction retried")
