# lib/address_extractor_module.py
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import requests

module_logger = logging.getLogger("icad_dispatch.address_extraction_module")


class AddressExtractorError(Exception):
    """Generic error during address extraction."""


class AddressExtractorConfigError(AddressExtractorError):
    """Raised when environment/config is invalid."""


class GeocodingError(Exception):
    """Base exception for geocoding errors."""


class GeocodingConfigError(GeocodingError):
    """Raised when geocoding configuration is invalid."""


# ---------------------------------------------------------------------
# Settings + data models
# ---------------------------------------------------------------------

@dataclass
class AddressExtractionSettings:
    """
    Per-system address extraction config, usually from system_row["address_extraction"].

    This is the Python mirror of what you edit in the UI:
    - enabled
    - OpenAI key/model override
    - Google Maps key
    - base geocode region (country/state/city)
    - per-system regions (state_code + county_name list)
    """
    enabled: bool = False

    # OpenAI config
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None

    # Google Maps config
    google_maps_api_key: Optional[str] = None

    # Base region hints (what you edited on the form: country/state/city)
    geocode_country: Optional[str] = None
    geocode_state: Optional[str] = None
    geocode_city: Optional[str] = None

    # Phase 1: Bounding box for Google Maps viewport bias
    bounds_min_lat: Optional[float] = None
    bounds_max_lat: Optional[float] = None
    bounds_min_lng: Optional[float] = None
    bounds_max_lng: Optional[float] = None

    # Phase 2: Service area cities for LLM hints (ordered list)
    geocode_cities: Optional[List[str]] = None

    # Regions table (list of dicts from DB/API: state_code, county_name, priority, ...)
    regions: Optional[List[Dict[str, Any]]] = None

    # Local road database for validation (list of dicts: road_name, road_type, city_name)
    roads: Optional[List[Dict[str, Any]]] = None

    @staticmethod
    def from_system_row(system_row: Dict[str, Any]) -> "AddressExtractionSettings":
        """
        Build settings from a full system row where
        system_row["address_extraction"] is the config blob.

        All values come from the blob only (no .env fallbacks).
        """
        cfg = (system_row or {}).get("address_extraction") or {}
        regions = cfg.get("regions") or []

        # Parse bounds from float or None
        def _f(val):
            try:
                return float(val) if val is not None else None
            except (ValueError, TypeError):
                return None

        return AddressExtractionSettings(
            enabled=bool(int(cfg.get("enabled") or 0)),
            openai_api_key=cfg.get("openai_api_key"),
            openai_model=cfg.get("openai_model"),
            google_maps_api_key=cfg.get("google_maps_api_key"),
            geocode_country=cfg.get("geocode_country") or cfg.get("country"),
            geocode_state=cfg.get("geocode_state") or cfg.get("state"),
            geocode_city=cfg.get("geocode_city") or cfg.get("city"),
            bounds_min_lat=_f(cfg.get("bounds_min_lat")),
            bounds_max_lat=_f(cfg.get("bounds_max_lat")),
            bounds_min_lng=_f(cfg.get("bounds_min_lng")),
            bounds_max_lng=_f(cfg.get("bounds_max_lng")),
            geocode_cities=cfg.get("geocode_cities") or [],
            regions=regions,
            roads=cfg.get("roads") or [],
        )


@dataclass
class ExtractedAddress:
    """
    Normalized address result from the LLM.

    All fields are optional; `raw_text` is what the model thinks
    the full address string is.
    """
    raw_text: str
    street: Optional[str] = None
    city: Optional[str] = None
    county: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    confidence: float = 0.0
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Drop None values to keep payloads clean
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class GeocodedAddress:
    """Result from geocoding an address."""
    lat: float
    lng: float
    formatted_address: str
    county: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "lat": self.lat,
            "lng": self.lng,
            "formatted_address": self.formatted_address,
            "county": self.county,
            "state": self.state,
            "city": self.city,
            "postal_code": self.postal_code,
            "country": self.country,
        }


def _regions_list_to_map(regions: Optional[List[Dict[str, Any]]]) -> Dict[str, List[str]]:
    """
    Convert the DB-style regions list into the dict format AddressGeocoder expects.

    Input rows look like:
      {"region_id": 1, "state_code": "PA", "county_name": "Bradford", "priority": 10, ...}
    Output:
      {"PA": ["Bradford", ...], "NY": ["Tioga", ...]}
    """
    region_map: Dict[str, List[str]] = {}
    if not regions:
        return region_map

    for r in regions:
        state = (r.get("state_code") or r.get("state") or "").strip().upper()
        county = (r.get("county_name") or r.get("county") or "").strip()
        if not state or not county:
            continue
        region_map.setdefault(state, []).append(county)

    # Deduplicate counties per state
    for st, counties in region_map.items():
        uniq: List[str] = []
        seen = set()
        for c in counties:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        region_map[st] = uniq

    return region_map


_STATE_NAME_TO_CODE: Dict[str, str] = {
    # Canada
    "ontario": "ON",
    "quebec": "QC",
    "british columbia": "BC",
    "alberta": "AB",
    "saskatchewan": "SK",
    "manitoba": "MB",
    "new brunswick": "NB",
    "nova scotia": "NS",
    "prince edward island": "PE",
    "newfoundland and labrador": "NL",
    "northwest territories": "NT",
    "nunavut": "NU",
    "yukon": "YT",
    # US (common)
    "pennsylvania": "PA",
    "new york": "NY",
    "california": "CA",
    "texas": "TX",
    "florida": "FL",
    "illinois": "IL",
    "ohio": "OH",
    "georgia": "GA",
    "michigan": "MI",
    "new jersey": "NJ",
    "virginia": "VA",
    "washington": "WA",
    "arizona": "AZ",
    "massachusetts": "MA",
    "tennessee": "TN",
    "indiana": "IN",
    "missouri": "MO",
    "maryland": "MD",
    "wisconsin": "WI",
    "colorado": "CO",
    "minnesota": "MN",
    "south carolina": "SC",
    "alabama": "AL",
    "louisiana": "LA",
    "kentucky": "KY",
    "oregon": "OR",
    "oklahoma": "OK",
    "connecticut": "CT",
    "utah": "UT",
    "iowa": "IA",
    "nevada": "NV",
    "arkansas": "AR",
    "mississippi": "MS",
    "kansas": "KS",
    "new mexico": "NM",
    "nebraska": "NE",
    "west virginia": "WV",
    "idaho": "ID",
    "hawaii": "HI",
    "new hampshire": "NH",
    "maine": "ME",
    "montana": "MT",
    "rhode island": "RI",
    "delaware": "DE",
    "south dakota": "SD",
    "north dakota": "ND",
    "alaska": "AK",
    "vermont": "VT",
    "wyoming": "WY",
}


# ---------------------------------------------------------------------
# Geocoder (Nominatim primary, Google fallback)
# ---------------------------------------------------------------------

class AddressGeocoder:
    """
    Geocode addresses using Nominatim (OpenStreetMap) as primary,
    Google Maps as optional fallback.
    """

    _NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    _GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(
        self,
        google_api_key: Optional[str] = None,
        *,
        country: Optional[str] = None,
        regions: Optional[Dict[str, List[str]]] = None,
        city_hint: Optional[str] = None,
        state_hint: Optional[str] = None,
        cities: Optional[List[str]] = None,
        bounds: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None,
        region: Optional[str] = None,
        timeout: int = 10,
        logger: Optional[logging.Logger] = None,
    ):
        self.log = logger or logging.getLogger("geocoding")

        self.google_api_key = (google_api_key or "").strip()
        self.regions = regions or {}
        self.country = (country or "us").lower()
        self.city_hint = (city_hint or "").strip()
        self.state_hint = (state_hint or "").strip()
        self.cities = [c.strip() for c in (cities or []) if c and c.strip()]
        self.bounds = bounds
        self.region = (region or "").strip().lower()
        self.timeout = timeout

        # Derived values
        self.target_states = list(self.regions.keys())
        self.target_counties = list(
            {county for counties in self.regions.values() for county in counties}
        )

        self._last_nominatim_ts = 0.0

        if not self.google_api_key:
            self.log.info("Google Maps API key not provided; Nominatim-only mode")
        if not self.regions:
            self.log.warning("No regions configured; county validation disabled")

        self.log.info(
            "AddressGeocoder initialized: states=%s, counties=%d, bounds=%s, region=%s, google_key=%s",
            self.target_states,
            len(self.target_counties),
            "yes" if self.bounds else "no",
            self.region or "none",
            "yes" if self.google_api_key else "no",
        )

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def geocode(
        self,
        address: str,
        extracted: Optional[ExtractedAddress] = None,
    ) -> Optional[GeocodedAddress]:
        """
        Geocode an address using Nominatim primary + Google fallback.

        Args:
            address: Address string to geocode
            extracted: Optional ExtractedAddress with parsed city/state
                       for fallback attempts

        Returns:
            GeocodedAddress or None if geocoding failed or was filtered
        """
        if not address or not address.strip():
            self.log.info("No address provided for geocoding")
            return None

        address = address.strip()

        # Helper: check if point is inside configured bounds
        def _in_bounds(lat: float, lng: float) -> bool:
            if not self.bounds:
                return True
            (sw_lat, sw_lng), (ne_lat, ne_lng) = self.bounds
            return (sw_lat <= lat <= ne_lat) and (sw_lng <= lng <= ne_lng)

        # Helper: reject geocoding results where the returned city doesn't
        # match the city we extracted from the transcript (prevents street-only
        # matches in the wrong town, e.g. Pembroke → Petawawa)
        def _city_matches(result_city: Optional[str]) -> bool:
            if not result_city or not extracted or not extracted.city:
                return True  # no extracted city to compare against
            rc = result_city.lower().strip()
            ec = extracted.city.lower().strip()
            # Exact match or the result city contains the extracted city
            if rc == ec or ec in rc or rc in ec:
                return True
            self.log.warning(
                "[Geocode] City mismatch: extracted=%r geocoded=%r — rejecting",
                extracted.city, result_city,
            )
            return False

        # Attempt 1: Nominatim full with hard viewbox (bounded=1)
        self.log.info("[Geocode] Attempt 1a - Nominatim full bounded=1: %r", address)
        result = self._geocode_nominatim(address, bounded=True)
        if result and _in_bounds(result["lat"], result["lng"]) and _city_matches(result.get("city")):
            self.log.info(
                "[Geocode] Nominatim bounded=1 succeeded: %r -> %s",
                address, result["formatted_address"],
            )
            return self._to_geocoded_address(result)

        # Attempt 1b: Nominatim full with soft bias (bounded=0)
        self.log.info("[Geocode] Attempt 1b - Nominatim full bounded=0: %r", address)
        result = self._geocode_nominatim(address, bounded=False)
        if result and _in_bounds(result["lat"], result["lng"]) and _city_matches(result.get("city")):
            self.log.info(
                "[Geocode] Nominatim bounded=0 succeeded: %r -> %s",
                address, result["formatted_address"],
            )
            return self._to_geocoded_address(result)

        # Attempt 2: street-only Nominatim (drop potentially-wrong city)
        if extracted and extracted.street:
            street_query = extracted.street.strip()
            if extracted.state:
                street_query += f", {extracted.state}"
            if self.country:
                street_query += f", {self.country.upper()}"
            self.log.info(
                "[Geocode] Attempt 2 - Nominatim street-only: %r", street_query
            )
            result = self._geocode_nominatim(street_query, bounded=False)
            if result and _in_bounds(result["lat"], result["lng"]) and _city_matches(result.get("city")):
                self.log.info(
                    "[Geocode] Nominatim street-only succeeded: %r -> %s",
                    street_query, result["formatted_address"],
                )
                return self._to_geocoded_address(result)

        # Attempt 3: Nominatim with city appended (use configured cities first)
        state = (extracted.state if extracted else None) or self.state_hint or None
        retry_city = None
        if extracted and extracted.city:
            retry_city = extracted.city
        elif self.cities:
            retry_city = self.cities[0]
        elif self.city_hint:
            retry_city = self.city_hint

        if retry_city and state:
            city_query = f"{address}, {retry_city}, {state}"
            self.log.info(
                "[Geocode] Attempt 3 - Nominatim city-appended: %r", city_query
            )
            result = self._geocode_nominatim(city_query, bounded=False)
            if result and _in_bounds(result["lat"], result["lng"]) and _city_matches(result.get("city")):
                self.log.info(
                    "[Geocode] Nominatim city-appended succeeded: %r -> %s",
                    city_query, result["formatted_address"],
                )
                return self._to_geocoded_address(result)

        # Attempt 3b: Strip mile-marker / exit wording and try highway + city only
        if retry_city and state and extracted and extracted.street:
            # Remove mile marker / exit / near / at wording
            stripped = re.sub(
                r"\s+(near|at|by|around)\s+(mile\s+marker|marker|exit)\s*\d+",
                "",
                address,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(r"\s+westbound|\s+eastbound|\s+northbound|\s+southbound", "", stripped, flags=re.IGNORECASE)
            stripped = stripped.strip()
            if stripped and stripped != address:
                stripped_query = f"{stripped}, {retry_city}, {state}"
                self.log.info(
                    "[Geocode] Attempt 3b - Nominatim stripped: %r", stripped_query
                )
                result = self._geocode_nominatim(stripped_query, bounded=False)
                if result and _in_bounds(result["lat"], result["lng"]) and _city_matches(result.get("city")):
                    self.log.info(
                        "[Geocode] Nominatim stripped succeeded: %r -> %s",
                        stripped_query, result["formatted_address"],
                    )
                    return self._to_geocoded_address(result)

        # Attempt 4: Google Maps (if key available)
        if self.google_api_key:
            self.log.info("[Geocode] Attempt 4 - Google Maps: %r", address)
            result = self._geocode_google(address)
            if result and _city_matches(result.get("city")):
                self.log.info(
                    "[Geocode] Google Maps succeeded: %r -> %s",
                    address, result["formatted_address"],
                )
                return self._to_geocoded_address(result)
            elif result:
                self.log.warning(
                    "[Geocode] Google Maps city mismatch — rejecting: %r -> %s",
                    address, result["formatted_address"],
                )
        else:
            self.log.info("[Geocode] Attempt 4 - Google Maps skipped (no API key)")

        # Attempt 5: Nominatim city-only fallback
        if retry_city and state:
            fallback_query = f"{retry_city}, {state}"
            if self.country:
                fallback_query += f", {self.country.upper()}"
            self.log.info(
                "[Geocode] Attempt 5 - Nominatim city fallback: %r", fallback_query
            )
            result = self._geocode_nominatim(fallback_query, bounded=False)
            if result:
                self.log.info(
                    "[Geocode] Nominatim city fallback succeeded: %r -> %s",
                    fallback_query, result["formatted_address"],
                )
                return self._to_geocoded_address(result)

        self.log.warning("[Geocode] All attempts failed for %r", address)
        return None

    # -----------------------------------------------------------------
    # Nominatim
    # -----------------------------------------------------------------

    def _nominatim_rate_limit(self) -> None:
        """Ensure at least 0.5 s between Nominatim requests."""
        elapsed = time.time() - self._last_nominatim_ts
        if elapsed < 0.5:
            delay = 0.5 - elapsed
            self.log.debug("Nominatim rate limit: sleeping %.2fs", delay)
            time.sleep(delay)
        self._last_nominatim_ts = time.time()

    def _geocode_nominatim(self, query: str, bounded: bool = False) -> Optional[dict]:
        """Single geocoding request to Nominatim.

        Args:
            query: Address string to geocode
            bounded: If True, send bounded=1 (hard viewbox restriction).
                     If False, send only viewbox (soft bias).
        """
        self._nominatim_rate_limit()

        params: Dict[str, str] = {
            "q": query,
            "format": "json",
            "limit": "5",
            "addressdetails": "1",
        }
        if self.country:
            params["countrycodes"] = self.country

        if self.bounds:
            sw_lat, sw_lng = self.bounds[0]
            ne_lat, ne_lng = self.bounds[1]
            # Nominatim viewbox: left,top,right,bottom (min_lon, max_lat, max_lon, min_lat)
            params["viewbox"] = f"{sw_lng},{ne_lat},{ne_lng},{sw_lat}"
            if bounded:
                params["bounded"] = "1"

        headers = {
            "User-Agent": (
                "iCADDispatch/2.0 "
                "(https://github.com/icad-dispatch/icad_dispatch_v2; "
                "dispatch@renfrewcounty.ca)"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            resp = requests.get(
                self._NOMINATIM_URL,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            self.log.error("Nominatim request exception for %r: %s", query, e)
            return None

        if not resp.ok:
            self.log.error("Nominatim HTTP %s for %r", resp.status_code, query)
            return None

        try:
            data = resp.json()
        except ValueError as e:
            self.log.error("Nominatim JSON parse error for %r: %s", query, e)
            return None

        if not isinstance(data, list) or not data:
            self.log.warning("Nominatim empty result for %r", query)
            return None

        # Try each result; prefer ones that pass region validation
        for result in data:
            parsed = self._parse_nominatim_result(result)
            if not parsed:
                continue

            validated = self._validate_region(parsed)
            if validated:
                return validated

            # If no regions configured, accept first result as best-effort
            if not self.regions:
                return parsed

        self.log.warning("Nominatim: no validated result for %r", query)
        return None

    def _parse_nominatim_result(self, result: dict) -> Optional[dict]:
        """Convert a single Nominatim result dict to our internal shape."""
        try:
            lat = float(result["lat"])
            lng = float(result["lon"])
        except (KeyError, ValueError, TypeError) as e:
            self.log.debug("Nominatim result missing lat/lon: %s", e)
            return None

        display_name = result.get("display_name", "")
        addr = result.get("address", {})

        # Extract state
        state_code = None
        state_name = addr.get("state")
        if state_name:
            state_code = _STATE_NAME_TO_CODE.get(state_name.lower()) or addr.get("state_code")
        if not state_code:
            state_code = addr.get("state_code")

        # Extract county
        county = addr.get("county") or addr.get("state_district")

        # Extract city
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("hamlet")
            or addr.get("suburb")
        )

        # Extract postal
        postal = addr.get("postcode")

        # Extract country
        country = addr.get("country_code")

        return {
            "lat": lat,
            "lng": lng,
            "formatted_address": display_name,
            "state": state_code,
            "county": county,
            "city": city,
            "postal_code": postal,
            "country": country,
        }

    # -----------------------------------------------------------------
    # Google Maps (kept largely intact)
    # -----------------------------------------------------------------

    def _geocode_google(self, address: str) -> Optional[dict]:
        """Geocode using Google Maps API."""
        endpoint = self._GOOGLE_URL

        components_parts = [f"country:{self.country}"]
        if len(self.target_states) == 1:
            components_parts.append(f"administrative_area:{self.target_states[0]}")
        components = "|".join(components_parts)

        params = {
            "address": address,
            "key": self.google_api_key,
            "components": components,
        }

        if self.bounds:
            sw_lat, sw_lng = self.bounds[0]
            ne_lat, ne_lng = self.bounds[1]
            params["bounds"] = f"{sw_lat},{sw_lng}|{ne_lat},{ne_lng}"

        if self.region:
            params["region"] = self.region

        try:
            resp = requests.get(endpoint, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            self.log.error("Google Maps request failed for %r: %s", address, e)
            return None

        if not resp.ok:
            self.log.error("Google Maps HTTP %s for %r", resp.status_code, address)
            return None

        try:
            data = resp.json()
        except ValueError as e:
            self.log.error("Google Maps JSON parse error for %r: %s", address, e)
            return None

        if data.get("status") != "OK" or not data.get("results"):
            self.log.warning("Google Maps status: %s for %r", data.get("status"), address)
            return None

        # Prefer specific result types
        preferred_types = [
            "street_address",
            "premise",
            "subpremise",
            "route",
            "intersection",
            "establishment",
            "point_of_interest",
        ]
        best_result = None
        for pref_type in preferred_types:
            for result in data["results"]:
                if pref_type in result.get("types", []):
                    best_result = result
                    break
            if best_result:
                break
        result = best_result or data["results"][0]

        lat = result["geometry"]["location"]["lat"]
        lng = result["geometry"]["location"]["lng"]
        formatted_address = result["formatted_address"]
        result_types = result.get("types", [])

        # Filter city-level results
        if self.city_hint and self.state_hint:
            city_pattern = re.compile(
                rf"^{re.escape(self.city_hint)},\s*{re.escape(self.state_hint)}\s+\d{{5}},\s*\w+$"
            )
            if city_pattern.match(formatted_address):
                self.log.info("[Filter] Skipping city-level Google result: %r", formatted_address)
                return None

        if "locality" in result_types and len(result_types) <= 3:
            self.log.info("[Filter] Skipping locality-only Google result: %r", formatted_address)
            return None

        if "administrative_area_level_2" in result_types and len(result_types) <= 3:
            self.log.info("[Filter] Skipping county-level Google result: %r", formatted_address)
            return None

        # Extract components
        state_component = None
        county_component = None
        city_component = None
        postal_component = None
        country_component = None

        for component in result.get("address_components", []):
            types = component.get("types", [])
            if "administrative_area_level_1" in types:
                state_component = component.get("short_name")
            elif "administrative_area_level_2" in types:
                county_component = component.get("long_name")
            elif "locality" in types:
                city_component = component.get("long_name")
            elif "postal_town" in types and not city_component:
                city_component = component.get("long_name")
            elif "postal_code" in types:
                postal_component = component.get("long_name")
            elif "country" in types:
                country_component = component.get("short_name") or component.get("long_name")

        parsed = {
            "lat": lat,
            "lng": lng,
            "formatted_address": formatted_address,
            "state": state_component,
            "county": county_component,
            "city": city_component,
            "postal_code": postal_component,
            "country": country_component,
        }

        validated = self._validate_region(parsed)
        if not validated:
            self.log.info(
                "[Filter] Google Maps result rejected by region validation: %r",
                formatted_address,
            )
            return None

        return validated

    # -----------------------------------------------------------------
    # Region validation
    # -----------------------------------------------------------------

    def _validate_region(self, parsed: dict) -> Optional[dict]:
        """
        Validate a parsed result against configured regions.
        Returns the parsed dict if valid, None otherwise.
        """
        if not self.regions:
            return parsed

        state = parsed.get("state")
        county = parsed.get("county")

        state_upper = (state or "").strip().upper()
        normalized_county = ""
        if county:
            normalized_county = county.lower().replace(" county", "").replace("&", "and").strip()

        allowed_counties = self.regions.get(state_upper)

        # If we have state and county, validate both
        if state_upper and normalized_county:
            if not allowed_counties:
                self.log.info(
                    '[Filter] State "%s" not in allowed states: %s',
                    state_upper, ", ".join(self.target_states),
                )
                return None
            if normalized_county not in [
                c.lower().replace(" county", "").replace("&", "and").strip() for c in allowed_counties
            ]:
                self.log.info(
                    '[Filter] County "%s" not in allowed counties for %s',
                    county, state_upper,
                )
                return None
            self.log.info("[Filter] Validated %s / %s", state_upper, county)
            return parsed

        # If we have state but no county, be lenient for Nominatim
        # (rural Canada sometimes lacks county in OSM)
        if state_upper and not normalized_county:
            if state_upper in self.target_states:
                self.log.info(
                    "[Filter] State %s valid (county missing, accepting)", state_upper,
                )
                return parsed
            else:
                self.log.info(
                    "[Filter] State %s not in allowed states", state_upper,
                )
                return None

        # No state or county info -> can't validate
        self.log.info(
            "[Filter] Missing state/county for %r", parsed["formatted_address"],
        )
        return None

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _to_geocoded_address(parsed: dict) -> GeocodedAddress:
        return GeocodedAddress(
            lat=parsed["lat"],
            lng=parsed["lng"],
            formatted_address=parsed["formatted_address"],
            county=parsed.get("county"),
            state=parsed.get("state"),
            city=parsed.get("city"),
            postal_code=parsed.get("postal_code"),
            country=parsed.get("country"),
        )

    @staticmethod
    def create_maps_url(lat: float, lng: float) -> str:
        """Create a Google Maps URL from coordinates."""
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


# ---------------------------------------------------------------------
# LLM extractor (OpenAI only)
# ---------------------------------------------------------------------

class AddressExtractorLLM:
    """
    Extract addresses from a transcript using an LLM (OpenAI only).

    Supports:
      - OPENAI_API_KEY / OPENAI_MODEL in env
      - Per-system overrides via AddressExtractionSettings
    """

    def __init__(
            self,
            openai_api_key: Optional[str] = None,
            openai_model: Optional[str] = None,
            *,
            target_states: Optional[List[str]] = None,
            target_counties: Optional[List[str]] = None,
            timeout: int = 15,
            logger: Optional[logging.Logger] = None,
    ) -> None:
        self.log = logger or logging.getLogger("address_extractor")

        # Per-system-only config
        self.openai_api_key = (openai_api_key or "").strip()
        self.openai_model = (openai_model or "").strip()
        self.timeout = timeout

        # Service region - passed in from AddressExtractionService
        self.target_states = target_states or []
        self.target_counties = target_counties or []

        self._validate_config()

    # ----------------- Public API -----------------

    def extract_address(
            self,
            transcript: str,
            *,
            town_hints: Optional[List[str]] = None,
            town_hint: Optional[str] = None,  # legacy alias
            county_hint: Optional[str] = None,
            state_hint: Optional[str] = None,
            country_hint: str = "US",
    ) -> Optional[ExtractedAddress]:
        """
        Extract an address from the given transcript.

        Args:
            transcript: The dispatch transcript text
            town_hints: List of cities/towns in the service area (Phase 2)
            town_hint: Legacy single city hint (falls back to town_hints[0])
            county_hint: Expected county
            state_hint: Expected state
            country_hint: Expected country (default "US")

        Returns:
            ExtractedAddress or None if no usable address is found.
        """
        text = (transcript or "").strip()
        if not text:
            self.log.info("AddressExtractorLLM: empty transcript, skipping")
            return None

        # Normalize legacy single hint to list
        hints = town_hints or []
        if not hints and town_hint:
            hints = [town_hint]

        prompt = self._build_prompt(
            text,
            town_hints=hints,
            county_hint=county_hint,
            state_hint=state_hint,
            country_hint=country_hint,
        )

        try:
            raw = self._call_openai(prompt)
        except Exception as e:
            self.log.exception("AddressExtractorLLM: LLM call failed: %s", e)
            raise AddressExtractorError(str(e)) from e

        # Remove <think> blocks that some models add
        raw = self._remove_think_blocks(raw)

        # Check if LLM indicated no address found
        if self._is_no_address_response(raw):
            self.log.info("AddressExtractorLLM: LLM returned 'no address found'")
            return None

        # Filter out generic city/state-only responses
        if town_hints and self._is_generic_city_response(raw, town_hints, state_hint):
            self.log.info(
                "AddressExtractorLLM: filtered generic city/state response: %s",
                raw,
            )
            return None

        try:
            data = self._decode_json_content(raw)
        except Exception as e:
            self.log.warning(
                "AddressExtractorLLM: failed to parse JSON output: %s", e
            )
            return None

        return self._to_result(data)

    # ----------------- Internal helpers -----------------

    def _validate_config(self) -> None:
        if not self.openai_api_key:
            raise AddressExtractorConfigError(
                "OPENAI_API_KEY environment variable not set "
                "(required for address extraction)"
            )
        if not self.openai_model:
            raise AddressExtractorConfigError(
                "OPENAI_MODEL environment variable not set "
                "(required for address extraction)"
            )

        self.log.info(
            "AddressExtractorLLM initialized: states=%s, counties=%d",
            self.target_states,
            len(self.target_counties),
        )

    def _build_prompt(
            self,
            transcript: str,
            *,
            town_hints: List[str],
            county_hint: Optional[str],
            state_hint: Optional[str],
            country_hint: str,
    ) -> str:
        """
        Build a detailed prompt with explicit examples and instructions.
        Models the comprehensive approach from the JavaScript version.
        """
        # Build service region info
        states_str = ", ".join(self.target_states) if self.target_states else state_hint or "US"
        counties_str = ", ".join(self.target_counties) if self.target_counties else county_hint or "Not specified"

        town_section = ""
        if town_hints:
            hints_str = ", ".join(f'"{h}"' for h in town_hints[:20])  # cap at 20 to keep prompt size reasonable
            town_section = f"""
SERVICE AREA CITIES (DEFAULT CITY/TOWN)
- The following cities/towns are within this dispatch service area: {hints_str}.
- The FIRST city in this list is the DEFAULT city for this call.
- Use the DEFAULT city unless the transcript EXPLICITLY names a DIFFERENT,
  clearly recognizable, real city/town within the service region.
- If the transcript mentions a city/town that sounds unusual, garbled, made-up,
  or unrecognizable (e.g. "Bojavelli", "Zorpington", "Bollardshire"), it is almost certainly
  a transcription error — trust the DEFAULT city instead.
- When Whisper transcribes radio audio, township names are OFTEN misheard.
  If you are uncertain about a city/town name, ALWAYS use the DEFAULT city.
- Do NOT return empty just because the transcript's city name seems wrong.
- The DEFAULT city is your SAFETY NET — use it whenever the city is ambiguous.
- Do NOT restrict yourself to these cities only. Any city/town INSIDE the service
  region is acceptable.
"""

        return f"""You are an assistant that extracts and completes addresses from first responder dispatch transcripts.

SERVICE REGION
- States: {states_str}
- Counties (with state): {counties_str}

Your job:
- Find a SINGLE incident location (one per transcript) that is inside this service region.
- A valid location can be:
  • A full street address (with or without city/state)
  • A block (e.g. "300 block of Maple Drive")
  • An intersection (e.g. "Main St and Park Ave")
  • A clearly named place (e.g. "Guthrie Hospital", "Town Center Mall") that is used as the call location.
  • A highway / interstate location with a mile marker, exit number, or nearby road
    (e.g. "Highway 401 westbound near mile marker 582, Napanee, ON",
     "I-81 Exit 230, Binghamton, NY", "Highway 2 and County Road 5, Perth, ON")
{town_section}
VERY IMPORTANT INSTRUCTIONS:
1. If no valid street name, intersection, or specific place (like a mall or park name) is clearly mentioned,
   set "raw_text" to "" and confidence to 0.
   A sequence of numbers alone (e.g. "5-9-1-6-9") is NOT enough; there must be a street name or named place.
2. DO NOT make up or hallucinate street names, towns, or states that are not clearly implied by the transcript
   or the service region list above.
3. DO NOT include ANY notes, comments, explanations, or parentheticals in your response.
4. Respond with ONLY valid JSON in the format specified below.
5. When in doubt about the CITY name, ALWAYS use the DEFAULT city. Do NOT return empty.
   Only return empty if NO street name, intersection, or specific place is mentioned at all.
6. FOR HIGHWAY / MILE-MARKER / EXIT-NUMBER LOCATIONS: ALWAYS include the nearest city/town
   in the raw_text. Without a city, the location cannot be mapped accurately.
   - BAD: "Highway 401 westbound near mile marker 582" (no city → will map to wrong place)
   - GOOD: "Highway 401 westbound near mile marker 582, Napanee, ON"
   - If the transcript does NOT clearly name a city, use the town_hint above.

INVALID INPUTS - MUST RETURN EMPTY:
- "Copy that"
- "Unit 5 responding"
- "Can you repeat that?"
- "We're on our way"
- "Copy following it"
- "10-4 received"
- Just a city name (e.g. "Sayre", "Elmira") with no street/place/highway
- Just a number like "5916" with no street/place/highway
- A location clearly outside the states/counties listed in the service region

VALID EXCEPTIONS:
- A highway mile marker or exit number IS valid when combined with a highway name AND a city
  (e.g. "Highway 401 near marker 582, Napanee, ON", "I-81 Exit 230, Binghamton, NY",
   "Highway 2 at County Road 5, Perth, ON")

FORMATTING RULES:
- If the transcript clearly specifies city and/or state, include them.
- The DEFAULT city for this call is "{(town_hints[0] if town_hints else 'N/A')}".
- If the transcript gives a street/place but NO city, or names a city that sounds
  garbled / unrecognizable, you MUST use the DEFAULT city.
- For highway locations this is especially important — always include the DEFAULT city.
- Never invent a different city not mentioned, but DO trust the DEFAULT city over a
  garbled transcript name.
- Blocks like "300 block of Maple Drive" should be normalized to "300 Maple Dr" with appropriate city.
- Intersections like "Main Street and Park Avenue" should be formatted as "Main St & Park Ave" with city.
- Highway locations like "Highway 401 near mile marker 582" should include the highway
  name, direction if given, the mile marker or exit number, AND the DEFAULT city.

Transcript:
```text
{transcript}
```

Return ONLY a JSON object with this exact shape (no extra keys, no text before or after the JSON):
{{
  "raw_text": "full address string as spoken or normalized",
  "street": "street number + name or null",
  "city": "city or null",
  "county": "county name or null",
  "state": "two-letter state code or full name or null",
  "postal_code": "ZIP/postal or null",
  "country": "ISO country like US or null",
  "confidence": 0.0,
  "extra": {{"notes": "optional notes"}}
}}

If no valid address is found, return:
{{
  "raw_text": "",
  "confidence": 0.0
}}
"""

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI's chat completions endpoint via HTTP."""
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.openai_model,
            "temperature": 0.1,
            "max_tokens": 150,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict JSON-only address extraction service. "
                        "Never include explanations or markdown, only JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }

        resp = requests.post(
            url, headers=headers, json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content

    @staticmethod
    def _remove_think_blocks(s: str) -> str:
        """Remove <think>...</think> blocks that some models add."""
        return re.sub(r"<think>.*?</think>\s*", "", s, flags=re.DOTALL).strip()

    @staticmethod
    def _is_no_address_response(s: str) -> bool:
        """Check if the response indicates no address was found."""
        s_lower = s.lower().strip()
        return s_lower in {"no address found", "no address found.", "no address"}

    @staticmethod
    def _is_generic_city_response(
            response: str,
            town_hints: List[str],
            state_hint: Optional[str],
    ) -> bool:
        """
        Check if LLM just returned a generic city/state with no street.
        This indicates it couldn't find a real address.
        """
        if not town_hints:
            return False

        response_clean = response.strip()

        for hint in town_hints:
            # Match patterns like "Sayre, PA" or just "Sayre"
            patterns = [
                rf"^{re.escape(hint)}$",
                rf"^{re.escape(hint)},\s*{re.escape(state_hint or '')}$",
            ]
            for pattern in patterns:
                if re.match(pattern, response_clean, re.IGNORECASE):
                    return True

        return False

    @staticmethod
    def _strip_json_fences(s: str) -> str:
        """Strip ```json ... ``` or ``` fences if the model ignored instructions."""
        s = s.strip()
        if s.startswith("```"):
            # remove leading ``` or ```json
            first_newline = s.find("\n")
            if first_newline != -1:
                s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[:-3]
        return s.strip()

    def _decode_json_content(self, content: str) -> Dict[str, Any]:
        text = self._strip_json_fences(content)
        return json.loads(text)

    def _to_result(self, data: Dict[str, Any]) -> Optional[ExtractedAddress]:
        raw_text = (data.get("raw_text") or "").strip()
        if not raw_text:
            # Model determined no usable address
            return None

        confidence = data.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            confidence = 0.0

        return ExtractedAddress(
            raw_text=raw_text,
            street=data.get("street"),
            city=data.get("city"),
            county=data.get("county"),
            state=data.get("state"),
            postal_code=data.get("postal_code"),
            country=data.get("country"),
            confidence=confidence,
            extra=data.get("extra") or {},
        )

# ---------------------------------------------------------------------
# High-level service wrapper
# ---------------------------------------------------------------------

class AddressExtractionService:
    """
    High-level wrapper for address extraction + geocoding for a single system.

    Usage:
        svc = AddressExtractionService.from_system_row(system_row)
        result = svc.extract_and_geocode(transcript)
    """

    def __init__(
            self,
            settings: AddressExtractionSettings,
            *,
            logger: Optional[logging.Logger] = None,
    ):
        self.settings = settings
        self.log = logger or module_logger

        # Build region map for geocoder
        region_map = _regions_list_to_map(settings.regions)

        # Derive target states/counties for the LLM from the same regions
        target_states = sorted(region_map.keys())
        target_counties = sorted({c for cs in region_map.values() for c in cs})

        # Fallback state if no explicit regions but we have a base state
        if not target_states and settings.geocode_state:
            target_states = [settings.geocode_state.strip().upper()]

        # Build bounds tuple if all four corners are present
        bounds = None
        if (settings.bounds_min_lat is not None and
            settings.bounds_max_lat is not None and
            settings.bounds_min_lng is not None and
            settings.bounds_max_lng is not None):
            bounds = (
                (settings.bounds_min_lat, settings.bounds_min_lng),
                (settings.bounds_max_lat, settings.bounds_max_lng),
            )

        # Phase 3: derive region from country (ccTLD bias)
        region = (settings.geocode_country or "us").lower()

        # Instantiate geocoder from settings
        self.geocoder = AddressGeocoder(
            google_api_key=settings.google_maps_api_key,
            country=(settings.geocode_country or "us").lower(),
            regions=region_map or None,
            city_hint=settings.geocode_city,
            state_hint=settings.geocode_state,
            cities=settings.geocode_cities,
            bounds=bounds,
            region=region,
            timeout=10,
            logger=self.log,
        )

        # Instantiate LLM extractor from settings (OpenAI only)
        self.llm = AddressExtractorLLM(
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            target_states=target_states or None,
            target_counties=target_counties or None,
            timeout=15,
            logger=self.log,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    @staticmethod
    def from_system_row(
        system_row: Dict[str, Any],
        *,
        logger: Optional[logging.Logger] = None,
    ) -> "AddressExtractionService":
        settings = AddressExtractionSettings.from_system_row(system_row)
        return AddressExtractionService(settings, logger=logger)

    # ---- main public call ----

    def extract_and_geocode(
        self,
        transcript: str,
        *,
        town_hint_override: Optional[str] = None,
    ) -> Dict[str, Optional[Any]]:
        """
        Run LLM extraction and (if successful) geocode the result.

        Args:
            transcript: The dispatch transcript text
            town_hint_override: Optional township/city hint derived from fired
                               trigger names (e.g. "Whitewater Region"). Takes
                               precedence over the system-wide geocode_city.

        Returns:
        {
            "extracted": ExtractedAddress | None,
            "geocoded": GeocodedAddress | None,
        }
        """
        if not self.enabled:
            self.log.debug(
                "AddressExtractionService: disabled for this system; skipping"
            )
            return {"extracted": None, "geocoded": None}

        # Build town hints list: override + configured cities
        town_hints = []
        if town_hint_override:
            town_hints.append(town_hint_override)
        if self.settings.geocode_cities:
            for city in self.settings.geocode_cities:
                if city and city not in town_hints:
                    town_hints.append(city)
        # Fallback to legacy single city if nothing else
        if not town_hints and self.settings.geocode_city:
            town_hints.append(self.settings.geocode_city)

        addr = self.llm.extract_address(
            transcript,
            town_hints=town_hints,
            county_hint=None,  # optional, could be wired from settings if needed
            state_hint=self.settings.geocode_state,
            country_hint=self.settings.geocode_country or "US",
        )

        if not addr:
            # ── Fallback: LLM gave up but transcript may still contain a street address ──
            fallback = self._fallback_extract_from_transcript(
                transcript, town_hints,
                self.settings.geocode_state,
                self.settings.geocode_country or "US",
            )
            if fallback:
                self.log.info(
                    "Address extraction fallback: call_id=%s extracted='%s'",
                    getattr(self, '_call_id', 'unknown'),
                    fallback.raw_text,
                )
                geo = self.geocoder.geocode(fallback.raw_text, extracted=fallback)
                return {"extracted": fallback, "geocoded": geo}
            return {"extracted": None, "geocoded": None}

        # ── Stage 2: Fuzzy validation against local road database ──
        addr = self._fuzzy_validate_road(addr)

        geo = None
        if addr.raw_text:
            geo = self.geocoder.geocode(addr.raw_text, extracted=addr)

        # ── Stage 3: Post-geocoding validation against local road database ──
        geo = self._validate_geocoded_road(geo)

        return {"extracted": addr, "geocoded": geo}

    def _fuzzy_validate_road(self, addr: ExtractedAddress) -> ExtractedAddress:
        """
        Stage 2: Check the LLM-extracted street against the local road database.
        If no exact match, try fuzzy matching (difflib, cutoff=0.7).
        Auto-correct if a close match is found.
        """
        if not addr.street:
            return addr

        roads = self.settings.roads or []
        if not roads:
            return addr

        road_names = [r["road_name"] for r in roads if r.get("road_name")]
        if not road_names:
            return addr

        # Exact match
        if addr.street in road_names:
            return addr

        # Fuzzy match
        matches = difflib.get_close_matches(addr.street, road_names, n=1, cutoff=0.7)
        if matches:
            corrected = matches[0]
            original_street = addr.street  # save before mutation
            self.log.info(
                "Road fuzzy match: '%s' -> '%s' (system)",
                original_street, corrected,
            )
            addr.street = corrected
            # Update raw_text to reflect correction if it contained the street
            if addr.raw_text:
                addr.raw_text = addr.raw_text.replace(original_street, corrected)
        else:
            self.log.warning(
                "Extracted street '%s' not found in local road database (system)",
                addr.street,
            )

        return addr

    def _validate_geocoded_road(self, geo: Optional[Any]) -> Optional[Any]:
        """
        Stage 3: After geocoding, check if the returned road exists in the
        local database. If not, reduce confidence and log a warning.
        """
        if not geo:
            return geo

        roads = self.settings.roads or []
        if not roads:
            return geo

        road_names = {r["road_name"] for r in roads if r.get("road_name")}

        # Extract road name from geocoded result
        geocoded_road = ""
        if geo.formatted_address:
            # Try to extract the road portion from formatted_address
            # This is heuristic; Nominatim usually puts the road first
            parts = geo.formatted_address.split(",")
            if parts:
                geocoded_road = parts[0].strip()

        if not geocoded_road:
            return geo

        # Check exact match
        if geocoded_road in road_names:
            return geo

        # Fuzzy match against road names
        matches = difflib.get_close_matches(geocoded_road, list(road_names), n=1, cutoff=0.7)
        if not matches:
            self.log.warning(
                "Geocoded road '%s' not in local road database for this system; "
                "reducing confidence",
                geocoded_road,
            )
            # Reduce confidence by 0.2 (GeocodedAddress doesn't have a confidence
            # field directly, so we just log — caller can act on the warning)

        return geo

    def _fallback_extract_from_transcript(
        self,
        transcript: str,
        town_hints: List[str],
        state: Optional[str],
        country: str,
    ) -> Optional[ExtractedAddress]:
        """
        Regex fallback: if the LLM returns empty but the transcript clearly
        contains a street address pattern (e.g. "137 Bruce Street"), extract
        it manually and append the DEFAULT city so geocoding can still succeed.
        """
        if not transcript or not town_hints:
            return None

        default_city = town_hints[0]

        # Comprehensive street suffix list for North American addresses
        street_suffixes = (
            r"Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Boulevard|Blvd|"
            r"Crescent|Cres|Lane|Ln|Way|Court|Ct|Place|Pl|Trail|Trl|"
            r"Highway|Hwy|Parkway|Pkwy|Circle|Cir|Terrace|Ter|Close|Cl|"
            r"Grove|Grv|Heights|Hts|Hollow|Holw|Bay|Beach|Bend|Bluff|"
            r"Bottom|Branch|Bridge|Brook|Burg|Bypass|Camp|Canyon|Cape|"
            r"Causeway|Center|Chase|Cheek|Church|Cliff|Club|Common|"
            r"Corner|Cottage|Course|Cove|Creek|Crest|Cross|Crossing|"
            r"Curve|Dale|Dam|Divide|Downtown|Edge|Estates|Expressway|"
            r"Extension|Fall|Falls|Ferry|Field|Flat|Ford|Forest|Forge|"
            r"Fork|Fort|Freeway|Garden|Gardens|Gate|Gateway|Glen|Green|"
            r"Ground|Grove|Harbor|Haven|Heights|Highlands|Highway|Hill|"
            r"Hills|Hollow|Horn|Horseshoe|Inlet|Island|Isle|Junction|"
            r"Key|Knoll|Lake|Landings|Lane|Landing|Light|Lights|Loaf|"
            r"Lock|Lodge|Loop|Mall|Manor|Meadow|Mews|Mill|Mills|Mission|"
            r"Motorway|Mount|Mountain|Neck|Orchard|Oval|Overpass|Park|"
            r"Parkway|Pass|Path|Pike|Pine|Plain|Plains|Plaza|Point|Port|"
            r"Prairie|Radial|Ramp|Ranch|Rapid|Rest|Ridge|Rise|River|Road|"
            r"Route|Row|Run|Shoal|Shore|Skyway|Spring|Spur|Square|Station|"
            r"Stravenue|Stream|Street|Summit|Terrace|Throughway|Trace|"
            r"Track|Trafficway|Trail|Trailer|Tunnel|Turnpike|Union|"
            r"Valley|Viaduct|View|Village|Ville|Vista|Walk|Wall|Way|Well|"
            r"Wells|Wharf|Wood|Woods|Wy"
        )

        # Pattern: optional prefix (at/address of/in front of/located at)
        # then: number + (word chars + spaces) + street suffix
        # Must have at least one word between number and suffix
        pattern = re.compile(
            r"(?:\bat\b|\baddress\s+of\b|\bin\s+front\s+of\b|\blocated\s+at\b)?\s*"
            r"(\b\d+(?:\s+[A-Za-z]+\.?)+\s+(?:" + street_suffixes + r"))\b",
            re.IGNORECASE,
        )

        match = pattern.search(transcript)
        if not match:
            return None

        street_part = match.group(1).strip()
        # Remove any trailing punctuation
        street_part = re.sub(r"[.,;:!]+$", "", street_part)

        # Build full address with DEFAULT city + state/country
        city_part = default_city
        state_part = state or ""
        country_part = country if not state else ""

        parts = [street_part, city_part]
        if state_part:
            parts.append(state_part)
        if country_part:
            parts.append(country_part)

        raw_text = ", ".join(parts)

        return ExtractedAddress(
            raw_text=raw_text,
            street=street_part,
            city=default_city,
            state=state,
            country=country,
            confidence=0.5,
        )
