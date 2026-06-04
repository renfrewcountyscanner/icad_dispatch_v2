PRAGMA foreign_keys = ON;
BEGIN;

-- per-system address extraction config
CREATE TABLE IF NOT EXISTS radio_system_address_extraction_settings
(
    address_extraction_setting_id       INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 1:1 with radio_systems
    radio_system_id      INTEGER NOT NULL UNIQUE
        REFERENCES radio_systems (radio_system_id)
            ON DELETE CASCADE,

    -- master switch
    address_extraction_enabled          INTEGER NOT NULL DEFAULT 0
        CHECK (address_extraction_enabled IN (0,1)),

    -- openai settings
    openai_api_key TEXT DEFAULT NULL,
    openai_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'
        CHECK (openai_model IN ('gpt-4o-mini','gpt-4o', 'gpt-4.1-mini','gpt-4.1')),

    -- Google Geocoding
    google_maps_api_key TEXT DEFAULT NULL,

    -- Geocoding Regions
    geocode_country TEXT NOT NULL DEFAULT 'US',
    geocode_state TEXT NOT NULL DEFAULT 'PA',
    geocode_city  TEXT NOT NULL DEFAULT 'Sayre'
);

-- Normalized geocoding regions (replaces the string column)
CREATE TABLE IF NOT EXISTS geocoding_regions (
    region_id INTEGER PRIMARY KEY AUTOINCREMENT,
    address_extraction_setting_id INTEGER NOT NULL
        REFERENCES radio_system_address_extraction_settings (address_extraction_setting_id)
            ON DELETE CASCADE,
    state_code TEXT NOT NULL,
    county_name TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    UNIQUE (address_extraction_setting_id, state_code, county_name)
);

CREATE INDEX IF NOT EXISTS idx_rsaes_radio_system_id
    ON radio_system_address_extraction_settings (radio_system_id);

CREATE INDEX IF NOT EXISTS idx_rsaes_enabled
    ON radio_system_address_extraction_settings (address_extraction_enabled);

CREATE INDEX IF NOT EXISTS idx_geocoding_regions_setting
    ON geocoding_regions (address_extraction_setting_id);

CREATE INDEX IF NOT EXISTS idx_geocoding_regions_state
    ON geocoding_regions (state_code);

COMMIT;
