#!/usr/bin/env python3
"""
Self-contained backfill script: Re-geocode existing calls using trigger-derived
-township hints. Uses sqlite3 directly and calls Google Maps API without
importing iCAD modules.

Usage:
    python backfill_trigger_township.py [--days N] [--dry-run] [--limit N]
"""

import os
import sys
import json
import time
import argparse
import logging
import sqlite3
import urllib.request
import urllib.parse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill")

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ICAD_DB_PATH", os.path.join(PROJECT_ROOT, "var", "icad_dispatch.db"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_google_api_key(conn):
    """Get the first available Google Maps API key from address extraction settings."""
    cur = conn.execute(
        "SELECT google_maps_api_key FROM radio_system_address_extraction_settings "
        "WHERE google_maps_api_key IS NOT NULL AND google_maps_api_key != '' "
        "LIMIT 1"
    )
    row = cur.fetchone()
    return row["google_maps_api_key"] if row else None


import re

BASE_SUFFIX_RE = re.compile(r"\s+Base$")


def clean_trigger_name(name):
    """Clean a trigger name to extract just the township/municipality."""
    name = name.strip()
    if not name:
        return ""
    # Strip prefixes
    for prefix in ("FIRE - ", "EMS - "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # Strip everything after " - " (sub-stations, etc.)
    if " - " in name:
        name = name.split(" - ")[0]
    # Strip " Base" suffix (EMS stations)
    name = BASE_SUFFIX_RE.sub("", name)
    return name.strip()


def derive_town_hint(conn, call_id):
    """Derive township hint from trigger names for a given call."""
    cur = conn.execute(
        """
        SELECT at.alert_trigger_name
        FROM trigger_fires tf
        JOIN alert_triggers at ON tf.alert_trigger_id = at.alert_trigger_id
        WHERE tf.call_id = ?
        ORDER BY at.alert_trigger_name
        """,
        (call_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    towns = []
    seen = set()
    for r in rows:
        name = clean_trigger_name(r["alert_trigger_name"] or "")
        if name and name not in seen:
            seen.add(name)
            towns.append(name)

    return ", ".join(towns) if towns else None


def address_needs_township(address, town_hint):
    """Check if the address already contains the township name."""
    if not address or not town_hint:
        return True
    address_lower = address.lower()
    # Split comma-separated town hints
    for town in town_hint.split(","):
        town = town.strip().lower()
        if town and town in address_lower:
            return False
    return True


def geocode_with_google(address, api_key, country="ca", state="ON"):
    """Geocode an address using Google Maps API."""
    endpoint = "https://maps.googleapis.com/maps/api/geocode/json"
    components = f"country:{country}"
    if state:
        components += f"|administrative_area:{state}"

    params = {
        "address": address,
        "key": api_key,
        "components": components,
    }

    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("Geocoding request failed for %r: %s", address, e)
        return None

    if data.get("status") != "OK":
        logger.warning("Geocoding status=%s for %r", data.get("status"), address)
        return None

    results = data.get("results", [])
    if not results:
        return None

    result = results[0]
    loc = result["geometry"]["location"]

    # Extract address components
    county = ""
    city = ""
    for comp in result.get("address_components", []):
        types = comp.get("types", [])
        if "administrative_area_level_2" in types:
            county = comp.get("long_name", "")
        elif "locality" in types or "sublocality" in types:
            city = comp.get("long_name", "")

    return {
        "lat": loc["lat"],
        "lng": loc["lng"],
        "formatted_address": result.get("formatted_address", ""),
        "county": county,
        "state": state,
        "city": city,
        "postal_code": "",
        "country": country.upper(),
        "maps_url": f"https://www.google.com/maps/search/?api=1&query={loc['lat']},{loc['lng']}",
    }


def regeocode_call(conn, call_id, api_key, town_hint, dry_run=False):
    """Re-geocode a single call using its existing extracted address + township hint."""
    cur = conn.execute(
        "SELECT address_extracted_json FROM call_transcripts WHERE call_id = ?",
        (call_id,),
    )
    row = cur.fetchone()
    if not row:
        return False

    extracted_json = row["address_extracted_json"]
    if not extracted_json:
        logger.debug("call_id=%s: no extracted address", call_id)
        return False

    try:
        extracted = json.loads(extracted_json)
    except Exception:
        logger.warning("call_id=%s: failed to parse extracted JSON", call_id)
        return False

    raw_text = extracted.get("raw_text", "").strip()
    if not raw_text:
        logger.debug("call_id=%s: empty raw_text", call_id)
        return False

    # Build improved query by appending township if not already present
    if address_needs_township(raw_text, town_hint):
        improved_query = f"{raw_text}, {town_hint}, ON, Canada"
    else:
        improved_query = f"{raw_text}, ON, Canada"

    geo = geocode_with_google(improved_query, api_key)
    if not geo:
        return False

    if dry_run:
        logger.info(
            "call_id=%s [DRY RUN] town_hint=%r query=%r -> lat=%s lng=%s addr=%r",
            call_id, town_hint, improved_query,
            geo["lat"], geo["lng"], geo["formatted_address"],
        )
        return True

    # Update DB
    conn.execute(
        "UPDATE call_transcripts SET address_geocoded_json = ? WHERE call_id = ?",
        (json.dumps(geo), call_id),
    )
    conn.commit()

    logger.info(
        "call_id=%s updated: town_hint=%r query=%r -> lat=%s lng=%s",
        call_id, town_hint, improved_query,
        geo["lat"], geo["lng"],
    )
    return True


def main():
    parser = argparse.ArgumentParser(description="Re-geocode calls with trigger-derived township hints")
    parser.add_argument("--days", type=int, default=7, help="Process calls from last N days")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing to DB")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N calls (0 = unlimited)")
    args = parser.parse_args()

    since_epoch = time.time() - (args.days * 86400)

    conn = get_db()

    api_key = get_google_api_key(conn)
    if not api_key:
        logger.error("No Google Maps API key found in address extraction settings")
        sys.exit(1)
    logger.info("Using Google Maps API key: %s...", api_key[:8])

    # Find calls with transcripts, triggers, and existing geocoded data
    cur = conn.execute(
        """
        SELECT cr.call_id
        FROM call_records cr
        JOIN call_transcripts ct ON cr.call_id = ct.call_id
        WHERE cr.start_epoch_s >= ?
          AND ct.address_extracted_json IS NOT NULL
          AND ct.address_extracted_json != ''
          AND EXISTS (
              SELECT 1 FROM trigger_fires tf WHERE tf.call_id = cr.call_id
          )
        ORDER BY cr.call_id DESC
        """,
        (since_epoch,),
    )
    calls = cur.fetchall()
    total = len(calls)
    logger.info("Found %d calls to process (last %d days)", total, args.days)

    if args.limit > 0:
        calls = calls[:args.limit]
        logger.info("Limited to %d calls", args.limit)

    updated = 0
    skipped = 0
    errors = 0

    for i, call in enumerate(calls, 1):
        call_id = call["call_id"]
        if i % 50 == 0:
            logger.info("Progress: %d/%d (%d updated)", i, len(calls), updated)

        town_hint = derive_town_hint(conn, call_id)
        if not town_hint:
            skipped += 1
            continue

        try:
            ok = regeocode_call(conn, call_id, api_key, town_hint, dry_run=args.dry_run)
            if ok:
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            logger.error("call_id=%s: unexpected error: %s", call_id, e)
            errors += 1

    logger.info(
        "Done: %d processed, %d updated, %d skipped, %d errors",
        len(calls), updated, skipped, errors,
    )


if __name__ == "__main__":
    main()
