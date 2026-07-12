"""Read-only operational metrics for the dashboard health view."""

from __future__ import annotations

import time
from typing import Any


def get_operations_status(db: Any, window_hours: int = 24) -> dict[str, Any]:
    """Return recent pipeline health and actionable address retry candidates."""
    window_hours = max(1, min(int(window_hours), 168))
    since = int(time.time()) - (window_hours * 3600)

    metrics = db.execute_query(
        """
        SELECT
            COUNT(cr.call_id) AS calls_received,
            COUNT(ct.call_id) AS calls_transcribed,
            SUM(CASE WHEN ct.address_extracted_json IS NOT NULL
                      AND ct.address_extracted_json <> '' THEN 1 ELSE 0 END) AS addresses_extracted,
            SUM(CASE WHEN ct.address_geocoded_json IS NOT NULL
                      AND ct.address_geocoded_json <> '' THEN 1 ELSE 0 END) AS addresses_geocoded,
            SUM(CASE WHEN ct.call_id IS NOT NULL
                      AND (ct.address_geocoded_json IS NULL OR ct.address_geocoded_json = '')
                     THEN 1 ELSE 0 END) AS geocode_pending,
            SUM(CASE WHEN ct.address_extracted_json IS NOT NULL
                      AND ct.address_extracted_json <> ''
                      AND COALESCE((ct.address_extracted_json::jsonb->>'confidence')::numeric, 0) < 0.75
                     THEN 1 ELSE 0 END) AS low_confidence_addresses,
            COUNT(cc.call_id) AS corrections_applied,
            MAX(cr.start_epoch_s) AS latest_call_epoch
        FROM call_records cr
        LEFT JOIN call_transcripts ct ON ct.call_id = cr.call_id
        LEFT JOIN call_corrections cc ON cc.call_id = cr.call_id
        WHERE cr.start_epoch_s >= ?
        """,
        (since,),
        fetch_mode="one",
    )
    if not metrics.get("success"):
        return {"success": False, "message": metrics.get("message", "Metrics query failed")}

    retries = db.execute_query(
        """
        SELECT cr.call_id, cr.start_epoch_s, cr.talkgroup_name, rs.system_name,
               ct.text_full
        FROM call_records cr
        JOIN call_transcripts ct ON ct.call_id = cr.call_id
        LEFT JOIN radio_systems rs ON rs.radio_system_id = cr.radio_system_id
        WHERE cr.start_epoch_s >= ?
          AND COALESCE(ct.text_full, '') <> ''
          AND (ct.address_geocoded_json IS NULL OR ct.address_geocoded_json = '')
        ORDER BY cr.start_epoch_s DESC
        LIMIT 25
        """,
        (since,),
        fetch_mode="all",
    )
    if not retries.get("success"):
        return {"success": False, "message": retries.get("message", "Retry query failed")}

    result = metrics["result"] or {}
    return {
        "success": True,
        "result": {
            "window_hours": window_hours,
            "since_epoch": since,
            "metrics": {
                key: int(result.get(key) or 0)
                for key in (
                    "calls_received", "calls_transcribed", "addresses_extracted",
                    "addresses_geocoded", "geocode_pending", "low_confidence_addresses", "corrections_applied",
                )
            } | {"latest_call_epoch": result.get("latest_call_epoch")},
            "retry_candidates": [
                {
                    "call_id": row["call_id"],
                    "start_epoch": row["start_epoch_s"],
                    "system_name": row.get("system_name") or "",
                    "talkgroup_name": row.get("talkgroup_name") or "",
                    "transcript": (row.get("text_full") or "").strip()[:180],
                }
                for row in retries["result"]
            ],
        },
    }
