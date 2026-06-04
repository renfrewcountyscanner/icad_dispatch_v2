PRAGMA foreign_keys = ON;
BEGIN;

-- Phase 2: Create geocoding_cities table
CREATE TABLE IF NOT EXISTS geocoding_cities (
    city_id INTEGER PRIMARY KEY AUTOINCREMENT,
    address_extraction_setting_id INTEGER NOT NULL
        REFERENCES radio_system_address_extraction_settings (address_extraction_setting_id)
            ON DELETE CASCADE,
    city_name TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    UNIQUE (address_extraction_setting_id, city_name)
);

CREATE INDEX IF NOT EXISTS idx_geocoding_cities_setting
    ON geocoding_cities (address_extraction_setting_id);

-- Auto-extract cities from existing geocoded calls
-- This inserts distinct city names found in geocoded addresses per system
INSERT INTO geocoding_cities (address_extraction_setting_id, city_name, priority)
SELECT DISTINCT
    aes.address_extraction_setting_id,
    json_extract(ct.address_geocoded_json, '$.city') AS city_name,
    10
FROM call_records cr
JOIN call_transcripts ct ON cr.call_id = ct.call_id
JOIN radio_system_address_extraction_settings aes ON cr.radio_system_id = aes.radio_system_id
WHERE ct.address_geocoded_json IS NOT NULL
  AND json_extract(ct.address_geocoded_json, '$.city') IS NOT NULL
  AND json_extract(ct.address_geocoded_json, '$.city') != ''
ORDER BY cr.radio_system_id, city_name;

COMMIT;
