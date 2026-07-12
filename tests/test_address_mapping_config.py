from lib.address_extractor_module import (
    AddressAlias,
    AddressExtractionService,
    AddressExtractionSettings,
    ExtractedAddress,
)
from lib.system_module import bulk_add_geocoding_roads


class _RoadBulkDb:
    def __init__(self):
        self.duplicate_check_params = None

    def begin(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute_query(self, _query, params, **_kwargs):
        self.duplicate_check_params = params
        return {"success": True, "result": None}

    def execute_commit(self, _query, _params, **_kwargs):
        return {"success": True}


def test_live_settings_accept_city_rows_and_road_rows():
    settings = AddressExtractionSettings.from_system_row({
        "address_extraction": {
            "enabled": 1,
            "cities": [{"city_name": "Whitewater Region"}, {"city_name": "Pembroke"}],
            "roads": [{"road_name": "Example Road"}],
        }
    })

    assert settings.geocode_cities == ["Whitewater Region", "Pembroke"]
    assert settings.roads == [{"road_name": "Example Road"}]


def test_address_alias_matches_heard_street_phrase():
    service = AddressExtractionService.__new__(AddressExtractionService)
    service.aliases = [AddressAlias("Ren Drive", "12 Real Road, Whitewater Region, ON", 45.0, -77.0)]

    match = service._matching_alias(ExtractedAddress(raw_text="12 Ren Drive, ON", street="12 Ren Drive"))

    assert match is not None
    assert match.canonical_address == "12 Real Road, Whitewater Region, ON"


def test_fuzzy_road_matching_ignores_house_number():
    service = AddressExtractionService.__new__(AddressExtractionService)
    service.settings = type("Settings", (), {"roads": [{"road_name": "Renn Drive"}]})()
    service.log = __import__("logging").getLogger("test")
    address = ExtractedAddress(raw_text="12 Ren Drive, ON", street="12 Ren Drive")

    corrected = service._fuzzy_validate_road(address)

    assert corrected.street == "12 Renn Drive"
    assert corrected.raw_text == "12 Renn Drive, ON"


def test_bulk_road_duplicate_check_binds_null_city_twice():
    db = _RoadBulkDb()

    result = bulk_add_geocoding_roads(db, 7, [{"road_name": "Example Road"}])

    assert result["success"] is True
    assert db.duplicate_check_params == (7, "Example Road", None, None)
