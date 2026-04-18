# lib/address_extractor_module.py
from __future__ import annotations

import json
import logging
import os
import re
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

    # Regions table (list of dicts from DB/API: state_code, county_name, priority, ...)
    regions: Optional[List[Dict[str, Any]]] = None

    @staticmethod
    def from_system_row(system_row: Dict[str, Any]) -> "AddressExtractionSettings":
        """
        Build settings from a full system row where
        system_row["address_extraction"] is the config blob.

        All values come from the blob only (no .env fallbacks).
        """
        cfg = (system_row or {}).get("address_extraction") or {}
        regions = cfg.get("regions") or []

        return AddressExtractionSettings(
            enabled=bool(int(cfg.get("enabled") or 0)),
            openai_api_key=cfg.get("openai_api_key"),
            openai_model=cfg.get("openai_model"),
            google_maps_api_key=cfg.get("google_maps_api_key"),
            geocode_country=cfg.get("geocode_country") or cfg.get("country"),
            geocode_state=cfg.get("geocode_state") or cfg.get("state"),
            geocode_city=cfg.get("geocode_city") or cfg.get("city"),
            regions=regions,
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


# ---------------------------------------------------------------------
# Geocoder (Google Maps only)
# ---------------------------------------------------------------------

class AddressGeocoder:
    """
    Geocode addresses using Google Maps.

    Supports multi-state, multi-county filtering with regional validation.

    Environment Variables (fallbacks):
        GOOGLE_MAPS_API_KEY: Google Maps API key
        GEOCODING_COUNTRY: Country code (default: 'us')
        GEOCODING_STATE: Fallback state code (e.g., 'PA')
        GEOCODING_CITY: Fallback city name
        GEOCODING_REGIONS: Multi-region config (e.g., "PA:Bradford,Tioga;NY:Chemung,Tioga")
        GEOCODING_TARGET_COUNTIES: Fallback counties (comma-separated)
    """

    def __init__(
            self,
            google_api_key: Optional[str] = None,
            *,
            country: Optional[str] = None,
            regions: Optional[Dict[str, List[str]]] = None,
            city_hint: Optional[str] = None,
            state_hint: Optional[str] = None,
            timeout: int = 10,
            logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the geocoder.

        Args:
            google_api_key: Google Maps API key
            country: Country code (default 'us')
            regions: Dict mapping state codes to county lists
            city_hint: Optional city to help filter city-level results
            state_hint: Optional state to help filter city-level results
            timeout: Request timeout in seconds
            logger: Custom logger instance
        """
        self.log = logger or logging.getLogger("geocoding")

        self.google_api_key = (google_api_key or "").strip()
        self.regions = regions or {}
        self.country = (country or "us").lower()
        self.city_hint = (city_hint or "").strip()
        self.state_hint = (state_hint or "").strip()
        self.timeout = timeout

        # Derived values
        self.target_states = list(self.regions.keys())
        self.target_counties = list(
            {county for counties in self.regions.values() for county in counties}
        )

        self._validate_config()

        self.log.info(
            "AddressGeocoder initialized: states=%s, counties=%d",
            self.target_states,
            len(self.target_counties),
        )

    def _validate_config(self) -> None:
        """Validate configuration."""
        if not self.google_api_key:
            raise GeocodingConfigError(
                "Google Maps API key is required but missing from the "
                "address_extraction config for this system."
            )

        if not self.regions:
            raise GeocodingConfigError(
                "No regions configured for this system. "
                "The address_extraction config must include a non-empty 'regions' list."
            )

    def geocode(self, address: str) -> Optional[GeocodedAddress]:
        """
        Geocode an address using Google Maps.

        Args:
            address: Address string to geocode

        Returns:
            GeocodedAddress or None if geocoding failed or was filtered
        """
        if not address or not address.strip():
            self.log.info("No address provided for geocoding")
            return None

        address = address.strip()
        return self._geocode_google(address)

    def _geocode_google(self, address: str) -> Optional[GeocodedAddress]:
        """
        Geocode using Google Maps API with fallback logic.

        If initial geocoding returns a result without county information,
        tries to pin the county by retrying with county names.
        """

        # Helper to normalize county names
        def normalize_county(name: str) -> str:
            return name.lower().replace(" county", "").strip()

        # Build normalized region map
        normalized_regions = {
            state: [normalize_county(c) for c in counties]
            for state, counties in self.regions.items()
        }

        # Geocode once
        def geocode_once(query: str) -> Optional[dict]:
            """Single geocoding request to Google Maps."""
            endpoint = "https://maps.googleapis.com/maps/api/geocode/json"

            # Build components filter
            components_parts = [f"country:{self.country}"]
            if len(self.target_states) == 1:
                components_parts.append(f"administrative_area:{self.target_states[0]}")
            components = "|".join(components_parts)

            params = {
                "address": query,
                "key": self.google_api_key,
                "components": components,
            }

            try:
                response = requests.get(
                    endpoint,
                    params=params,
                    timeout=self.timeout,
                )

                if not response.ok:
                    self.log.error(
                        "Google Maps API error: %s for %r",
                        response.status_code,
                        query,
                    )
                    return None

                data = response.json()

                if data.get("status") != "OK" or not data.get("results"):
                    self.log.warning(
                        "Google Maps status: %s for %r",
                        data.get("status"),
                        query,
                    )
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

                # Filter out city-level results if we have a specific city hint
                geocoding_city = self.city_hint or ""
                geocoding_state = self.state_hint or ""

                if geocoding_city and geocoding_state:
                    # Pattern: "City, ST 12345, USA"
                    city_pattern = re.compile(
                        rf"^{re.escape(geocoding_city)}, {re.escape(geocoding_state)} \d{{5}}, USA$"
                    )
                    if city_pattern.match(formatted_address):
                        self.log.info(
                            "[Filter] Skipping city-level: %r", formatted_address
                        )
                        return None

                # Filter locality-only results
                if "locality" in result_types and len(result_types) <= 3:
                    self.log.info(
                        "[Filter] Skipping locality-only: %r", formatted_address
                    )
                    return None

                # Filter county-level results
                if (
                        "administrative_area_level_2" in result_types
                        and len(result_types) <= 3
                ):
                    self.log.info(
                        "[Filter] Skipping county-level: %r", formatted_address
                    )
                    return None

                    # Extract state and county components
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
                        # UK-style, but harmless as a fallback
                        city_component = component.get("long_name")
                    elif "postal_code" in types:
                        postal_component = component.get("long_name")
                    elif "country" in types:
                        country_component = component.get("short_name") or component.get("long_name")


                return {
                    "lat": lat,
                    "lng": lng,
                    "formatted_address": formatted_address,
                    "state": state_component,
                    "county": county_component,
                    "city": city_component,
                    "postal_code": postal_component,
                    "country": country_component,
                }

            except requests.RequestException as e:
                self.log.error("Google Maps request failed for %r: %s", query, e)
                return None
            except (KeyError, ValueError) as e:
                self.log.error("Error parsing Google Maps response: %s", e)
                return None

        # Primary geocode
        primary = geocode_once(address)
        if not primary:
            return None

        lat = primary["lat"]
        lng = primary["lng"]
        formatted_address = primary["formatted_address"]
        state_code = primary.get("state")
        county_raw = primary.get("county")

        # Case 1: Both state and county present - just validate
        if state_code and county_raw:
            state_upper = state_code.upper()
            normalized_county = normalize_county(county_raw)

            allowed_counties = normalized_regions.get(state_upper)

            if not allowed_counties:
                self.log.warning(
                    '[Filter] State "%s" not in allowed states: %s',
                    state_upper,
                    ", ".join(self.target_states),
                )
                return None

            if normalized_county not in allowed_counties:
                self.log.warning(
                    '[Filter] County "%s" not in allowed counties for %s: %s',
                    county_raw,
                    state_upper,
                    ", ".join(self.regions[state_upper]),
                )
                return None

            # Success
            self.log.info(
                'Geocoded: %r → (%s, %s) in %s',
                formatted_address,
                lat,
                lng,
                county_raw,
            )
            return GeocodedAddress(
                lat=lat,
                lng=lng,
                formatted_address=formatted_address,
                county=county_raw,
                state=state_code,
                city=primary.get("city"),
                postal_code=primary.get("postal_code"),
                country=primary.get("country"),
            )

        # Case 2: State present but county missing - try fallback with counties
        if state_code and not county_raw:
            state_upper = state_code.upper()

            if state_upper in self.target_states:
                allowed_counties = self.regions.get(state_upper, [])

                # Extract just the street part from original address
                street_part = address.split(",")[0].strip()

                # Try each county
                for county_name in allowed_counties:
                    candidate = f"{street_part}, {county_name} County, {state_upper}"

                    self.log.warning(
                        '[Fallback] Retrying with %r to pin county', candidate
                    )

                    fallback = geocode_once(candidate)
                    if not fallback:
                        continue

                    fb_state = fallback["state"]
                    fb_county = fallback["county"]

                    if not fb_state or not fb_county:
                        continue

                    fb_state_upper = fb_state.upper()
                    fb_normalized_county = normalize_county(fb_county)
                    fb_allowed = normalized_regions.get(fb_state_upper)

                    if not fb_allowed:
                        continue

                    if fb_normalized_county not in fb_allowed:
                        continue

                    # First valid fallback wins
                    self.log.info(
                        "Geocoded (fallback): %r → (%s, %s) in %s",
                        fallback["formatted_address"],
                        fallback["lat"],
                        fallback["lng"],
                        fb_county,
                    )
                    return GeocodedAddress(
                        lat=fallback["lat"],
                        lng=fallback["lng"],
                        formatted_address=fallback["formatted_address"],
                        county=fb_county,
                        state=fallback.get("state"),
                        city=fallback.get("city"),
                        postal_code=fallback.get("postal_code"),
                        country=fallback.get("country"),
                    )

            # Fallback failed
            self.log.warning(
                "[Filter] Could not determine valid county for %r", formatted_address
            )
            return None

        # Case 3: No state at all - give up
        self.log.warning(
            "[Filter] Missing state/county for %r", formatted_address
        )
        return None

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
            town_hint: Optional[str] = None,
            county_hint: Optional[str] = None,
            state_hint: Optional[str] = None,
            country_hint: str = "US",
    ) -> Optional[ExtractedAddress]:
        """
        Extract an address from the given transcript.

        Args:
            transcript: The dispatch transcript text
            town_hint: The town/city to use as a default when completing addresses
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

        prompt = self._build_prompt(
            text,
            town_hint=town_hint,
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
        if town_hint and self._is_generic_city_response(raw, town_hint, state_hint):
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
            town_hint: Optional[str],
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
        if town_hint:
            town_section = f"""
TOWN HINT (NOT A HARD FILTER)
- The dispatch/talkgroup is associated with "{town_hint}".
- Use "{town_hint}" ONLY as a hint to complete an address when:
  • The transcript clearly gives a street / intersection / place name
  • BUT does NOT clearly specify a city/town.
- If the transcript clearly mentions a different city/town, use THAT city/town instead of "{town_hint}".
- Do NOT restrict yourself to "{town_hint}" only. Any city/town INSIDE the service region is acceptable.
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
{town_section}
VERY IMPORTANT INSTRUCTIONS:
1. If no valid street name, intersection, or specific place (like a mall or park name) is clearly mentioned,
   set "raw_text" to "" and confidence to 0.
   A sequence of numbers alone (e.g. "5-9-1-6-9") is NOT enough; there must be a street name or named place.
2. DO NOT make up or hallucinate street names, towns, or states that are not clearly implied by the transcript
   or the service region list above.
3. DO NOT include ANY notes, comments, explanations, or parentheticals in your response.
4. Respond with ONLY valid JSON in the format specified below.
5. When in doubt, prefer empty raw_text and confidence=0 rather than guessing.

INVALID INPUTS - MUST RETURN EMPTY:
- "Copy that"
- "Unit 5 responding"
- "Can you repeat that?"
- "We're on our way"
- "Copy following it"
- "10-4 received"
- Just a city name (e.g. "Sayre", "Elmira") with no street/place
- Just a number like "5916" with no street/place
- A location clearly outside the states/counties listed in the service region

FORMATTING RULES:
- If the transcript clearly specifies city and/or state, include them.
- If the transcript gives a street/place but NO city and town_hint is "{town_hint or 'N/A'}":
  • You MAY complete it with that town if reasonable, but never invent a different city not mentioned.
- Blocks like "300 block of Maple Drive" should be normalized to "300 Maple Dr" with appropriate city.
- Intersections like "Main Street and Park Avenue" should be formatted as "Main St & Park Ave" with city.

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
            town_hint: Optional[str],
            state_hint: Optional[str],
    ) -> bool:
        """
        Check if LLM just returned the generic city/state with no street.
        This indicates it couldn't find a real address.
        """
        if not town_hint:
            return False

        response_clean = response.strip()

        # Match patterns like "Sayre, PA" or just "Sayre"
        patterns = [
            rf"^{re.escape(town_hint)}$",
            rf"^{re.escape(town_hint)},\s*{re.escape(state_hint or '')}$",
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

        # Instantiate geocoder from settings (Google Maps only)
        self.geocoder = AddressGeocoder(
            google_api_key=settings.google_maps_api_key,
            country=(settings.geocode_country or "us").lower(),
            regions=region_map or None,
            city_hint=settings.geocode_city,
            state_hint=settings.geocode_state,
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
    ) -> Dict[str, Optional[Any]]:
        """
        Run LLM extraction and (if successful) geocode the result.

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

        addr = self.llm.extract_address(
            transcript,
            town_hint=self.settings.geocode_city,
            county_hint=None,  # optional, could be wired from settings if needed
            state_hint=self.settings.geocode_state,
            country_hint=self.settings.geocode_country or "US",
        )

        if not addr:
            return {"extracted": None, "geocoded": None}

        geo = None
        if addr.raw_text:
            geo = self.geocoder.geocode(addr.raw_text)

        return {"extracted": addr, "geocoded": geo}
