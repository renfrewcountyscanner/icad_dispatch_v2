from lib import osm_roads_module


def test_large_bounds_are_split_and_roads_deduplicated(monkeypatch):
    calls = []

    def fake_fetch(south, west, north, east):
        calls.append((south, west, north, east))
        return [
            {"name": "Example Road", "type": "residential", "city": None, "osm_way_id": 1},
        ]

    monkeypatch.setattr(osm_roads_module, "fetch_roads_from_overpass", fake_fetch)

    roads = osm_roads_module.fetch_roads_for_bounds(45.0, -77.0, 45.8, -76.2, max_span_degrees=0.35)

    assert len(calls) == 9
    assert len(roads) == 1
