-- 031_add_geocoding_roads.sql
-- OSM road database for local address extraction validation.
-- Used for fuzzy matching extracted street names and post-geocoding sanity checks.
-- PostgreSQL-only; no SQLite equivalent needed.

CREATE TABLE IF NOT EXISTS geocoding_roads (
    road_id                         SERIAL PRIMARY KEY,
    address_extraction_setting_id   INTEGER NOT NULL
        REFERENCES radio_system_address_extraction_settings (address_extraction_setting_id)
            ON DELETE CASCADE,
    road_name                       TEXT NOT NULL,
    road_type                       TEXT,            -- OSM highway tag value (trunk, primary, residential, ...)
    city_name                       TEXT,
    priority                        INTEGER NOT NULL DEFAULT 0,
    osm_way_id                      BIGINT,
    UNIQUE (address_extraction_setting_id, road_name, city_name)
);

CREATE INDEX idx_geocoding_roads_setting ON geocoding_roads (address_extraction_setting_id);
CREATE INDEX idx_geocoding_roads_name    ON geocoding_roads (road_name);

-- Track that this migration has been applied
INSERT INTO schema_migrations (version) VALUES (31)
    ON CONFLICT DO NOTHING;
