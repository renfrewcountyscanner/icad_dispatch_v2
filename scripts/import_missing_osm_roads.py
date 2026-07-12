#!/usr/bin/env python3
"""Import OSM road vocabulary for systems that have bounds but no roads."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.osm_roads_module import preview_roads_for_bounds
from lib.postgres_module import PostgreSQLDatabase
from lib.system_module import bulk_add_geocoding_roads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--system",
        type=int,
        action="append",
        dest="system_ids",
        help="Radio system ID to import. Repeat for multiple systems.",
    )
    parser.add_argument(
        "--source",
        choices=("osm", "nominatim"),
        default="osm",
        help="Road source. Nominatim credentials are read from NOMINATIM_DB_* environment variables.",
    )
    return parser.parse_args()


def fetch_roads_from_nominatim(system: dict) -> list[dict]:
    """Read named highway records directly from a local Nominatim database."""
    password = os.getenv("NOMINATIM_DB_PASSWORD")
    if not password:
        raise RuntimeError("NOMINATIM_DB_PASSWORD is required for --source nominatim")

    connection = psycopg2.connect(
        host=os.getenv("NOMINATIM_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("NOMINATIM_DB_PORT", "5432")),
        dbname=os.getenv("NOMINATIM_DB_NAME", "nominatim"),
        user=os.getenv("NOMINATIM_DB_USER", "nominatim"),
        password=password,
        connect_timeout=15,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT
                    name -> 'name' AS road_name,
                    type AS road_type,
                    COALESCE(address -> 'city', address -> 'town',
                             address -> 'village', address -> 'municipality') AS city_name
                FROM placex
                WHERE class = 'highway'
                  AND type = ANY(%s)
                  AND name ? 'name'
                  AND ST_Intersects(
                      geometry,
                      ST_MakeEnvelope(%s, %s, %s, %s, 4326)
                  )
                ORDER BY road_name, road_type, city_name
                """,
                (
                    [
                        "motorway", "motorway_link", "trunk", "trunk_link",
                        "primary", "primary_link", "secondary", "secondary_link",
                        "tertiary", "tertiary_link", "residential", "unclassified",
                        "living_street", "service",
                    ],
                    float(system["bounds_min_lng"]),
                    float(system["bounds_min_lat"]),
                    float(system["bounds_max_lng"]),
                    float(system["bounds_max_lat"]),
                ),
            )
            return [
                {"road_name": row[0], "road_type": row[1], "city_name": row[2]}
                for row in cursor.fetchall()
                if row[0]
            ]
    finally:
        connection.close()


def configured_systems(db: PostgreSQLDatabase, system_ids: list[int] | None) -> list[dict]:
    params: list[int] = []
    filter_sql = ""
    if system_ids:
        filter_sql = "AND aes.radio_system_id IN ({})".format(", ".join("?" for _ in system_ids))
        params.extend(system_ids)

    result = db.execute_query(
        f"""
        SELECT aes.radio_system_id, rs.system_name, aes.address_extraction_setting_id,
               aes.bounds_min_lat, aes.bounds_max_lat, aes.bounds_min_lng, aes.bounds_max_lng,
               COUNT(gr.road_id) AS road_count
        FROM radio_system_address_extraction_settings aes
        JOIN radio_systems rs ON rs.radio_system_id = aes.radio_system_id
        LEFT JOIN geocoding_roads gr
          ON gr.address_extraction_setting_id = aes.address_extraction_setting_id
        WHERE aes.bounds_min_lat IS NOT NULL AND aes.bounds_max_lat IS NOT NULL
          AND aes.bounds_min_lng IS NOT NULL AND aes.bounds_max_lng IS NOT NULL
          {filter_sql}
        GROUP BY aes.radio_system_id, rs.system_name, aes.address_extraction_setting_id,
                 aes.bounds_min_lat, aes.bounds_max_lat, aes.bounds_min_lng, aes.bounds_max_lng
        HAVING COUNT(gr.road_id) = 0
        ORDER BY aes.radio_system_id
        """,
        tuple(params),
        fetch_mode="all",
    )
    if not result.get("success"):
        raise RuntimeError(result.get("message") or "Could not load systems")
    return result.get("result") or []


def main() -> int:
    args = parse_args()
    db = PostgreSQLDatabase()
    systems = configured_systems(db, args.system_ids)
    if not systems:
        print("No systems with configured bounds and an empty road vocabulary.")
        return 0

    for system in systems:
        system_id = system["radio_system_id"]
        print(f"Fetching {args.source} roads for system {system_id}: {system['system_name']}")
        if args.source == "nominatim":
            payload = fetch_roads_from_nominatim(system)
        else:
            roads, _ = preview_roads_for_bounds(
                float(system["bounds_min_lat"]),
                float(system["bounds_min_lng"]),
                float(system["bounds_max_lat"]),
                float(system["bounds_max_lng"]),
            )
            payload = [
                {
                    "road_name": road["name"],
                    "road_type": road.get("type"),
                    "city_name": road.get("city"),
                }
                for road in roads
                if road.get("name")
            ]
        response = bulk_add_geocoding_roads(
            db,
            int(system["address_extraction_setting_id"]),
            payload,
        )
        if not response.get("success"):
            print(f"System {system_id} failed: {response.get('message')}", file=sys.stderr)
            return 1
        print(f"System {system_id}: {response.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
