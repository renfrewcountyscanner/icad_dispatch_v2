"""OSM Road Database — Overpass API client and helpers.

Fetches road names from OpenStreetMap via Overpass API for a given bounding box.
Used to populate the geocoding_roads table per radio system.

Rate-limited to 1 req/sec to be polite to public Overpass servers.
"""

import json
import logging
import time
from typing import Dict, List, Optional, Tuple

import requests

module_logger = logging.getLogger("icad_dispatch.osm_roads")

# Overpass API endpoint
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM highway tags we care about (exclude footways, paths, tracks, cycleways)
_INCLUDED_HIGHWAY_TYPES = frozenset([
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "residential", "unclassified",
    "living_street",
    "service",
])

# Rate-limit state
_last_overpass_ts = 0.0


def _rate_limit(min_interval: float = 1.0) -> None:
    """Enforce minimum delay between Overpass requests."""
    global _last_overpass_ts
    elapsed = time.time() - _last_overpass_ts
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_overpass_ts = time.time()


def build_overpass_query(
    south: float,
    west: float,
    north: float,
    east: float,
) -> str:
    """
    Build an Overpass QL query that returns all named highway ways
    inside the bounding box.
    """
    # Convert bbox to Overpass syntax: (south,west,north,east)
    bbox = f"({south},{west},{north},{east})"
    return f"""
        [out:json][timeout:60];
        way["highway"]{bbox};
        out tags;
    """


def fetch_roads_from_overpass(
    south: float,
    west: float,
    north: float,
    east: float,
    timeout: float = 120.0,
) -> List[Dict[str, Optional[str]]]:
    """
    Query Overpass API for road names within a bounding box.

    Returns a deduplicated list of dicts:
        [{"name": str, "type": str, "city": str|None, "osm_way_id": int}, ...]

    Raises RuntimeError on network or Overpass errors.
    """
    _rate_limit(min_interval=1.0)

    query = build_overpass_query(south, west, north, east)

    module_logger.info(
        "Overpass query: bbox=(%.5f, %.5f, %.5f, %.5f)",
        south, west, north, east,
    )

    try:
        resp = requests.post(
            _OVERPASS_URL,
            data={"data": query},
            headers={
                "User-Agent": (
                    "iCADDispatch/2.0 "
                    "(https://github.com/icad-dispatch/icad_dispatch_v2)"
                ),
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Overpass request failed: {e}")

    try:
        data = resp.json()
    except requests.JSONDecodeError as e:
        raise RuntimeError(
            f"Overpass returned non-JSON response (status {resp.status_code}): {e}"
        )

    elements = data.get("elements", [])

    roads: Dict[Tuple[str, str, Optional[str]], Dict[str, Optional[str]]] = {}

    for el in elements:
        if el.get("type") != "way":
            continue

        tags = el.get("tags", {})
        highway_type = tags.get("highway", "")
        name = tags.get("name", "").strip()

        if not highway_type or not name:
            continue
        if highway_type not in _INCLUDED_HIGHWAY_TYPES:
            continue

        # Optional city/township from OSM addr tags
        city = (
            tags.get("addr:city")
            or tags.get("addr:hamlet")
            or tags.get("addr:village")
            or tags.get("addr:town")
            or None
        )
        if city:
            city = city.strip()

        osm_way_id = el.get("id")
        key = (name, highway_type, city)

        if key not in roads:
            roads[key] = {
                "name": name,
                "type": highway_type,
                "city": city,
                "osm_way_id": osm_way_id,
            }

    result = list(roads.values())
    module_logger.info(
        "Overpass returned %d elements, %d unique named roads",
        len(elements),
        len(result),
    )
    return result


def preview_roads_for_bounds(
    south: float,
    west: float,
    north: float,
    east: float,
) -> Tuple[List[Dict[str, Optional[str]]], int]:
    """
    Fetch roads and return them along with the raw count.

    Returns (roads_list, total_count).
    """
    roads = fetch_roads_from_overpass(south, west, north, east)
    return roads, len(roads)
