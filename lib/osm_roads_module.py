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

# Overpass API endpoints. Public instances can be temporarily overloaded, so
# try a second independent instance before treating a road preview as failed.
_OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

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

    response_errors: List[str] = []
    resp = None
    for endpoint in _OVERPASS_URLS:
        try:
            candidate = requests.post(
                endpoint,
                data={"data": query},
                headers={
                    "User-Agent": (
                        "iCADDispatch/2.0 "
                        "(https://github.com/icad-dispatch/icad_dispatch_v2)"
                    ),
                },
                timeout=timeout,
            )
            candidate.raise_for_status()
            resp = candidate
            break
        except requests.RequestException as e:
            response_errors.append(f"{endpoint}: {e}")
            module_logger.warning("Overpass request failed for %s: %s", endpoint, e)

    if resp is None:
        raise RuntimeError("Overpass request failed: " + "; ".join(response_errors))

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


def fetch_roads_for_bounds(
    south: float,
    west: float,
    north: float,
    east: float,
    max_span_degrees: float = 0.20,
) -> List[Dict[str, Optional[str]]]:
    """Fetch roads for a bounding box, splitting county-scale areas into tiles.

    Public Overpass instances routinely time out for wide rural service areas.
    Each tile stays small enough for a predictable response, then the results are
    deduplicated using the same key as a single Overpass response.
    """
    if south >= north or west >= east:
        raise ValueError("south < north and west < east are required")
    if max_span_degrees <= 0:
        raise ValueError("max_span_degrees must be positive")

    lat_steps = max(1, int((north - south) / max_span_degrees) + 1)
    lng_steps = max(1, int((east - west) / max_span_degrees) + 1)
    lat_size = (north - south) / lat_steps
    lng_size = (east - west) / lng_steps
    unique: Dict[Tuple[str, str, Optional[str]], Dict[str, Optional[str]]] = {}

    for lat_index in range(lat_steps):
        tile_south = south + lat_index * lat_size
        tile_north = north if lat_index == lat_steps - 1 else tile_south + lat_size
        for lng_index in range(lng_steps):
            tile_west = west + lng_index * lng_size
            tile_east = east if lng_index == lng_steps - 1 else tile_west + lng_size
            for road in fetch_roads_from_overpass(tile_south, tile_west, tile_north, tile_east):
                key = (road["name"], road["type"], road.get("city"))
                unique.setdefault(key, road)

    return list(unique.values())


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
    roads = fetch_roads_for_bounds(south, west, north, east)
    return roads, len(roads)
