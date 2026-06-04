PRAGMA foreign_keys = ON;
BEGIN;

-- Phase 1: Add bounding box columns
ALTER TABLE radio_system_address_extraction_settings
    ADD COLUMN bounds_min_lat REAL DEFAULT NULL;
ALTER TABLE radio_system_address_extraction_settings
    ADD COLUMN bounds_max_lat REAL DEFAULT NULL;
ALTER TABLE radio_system_address_extraction_settings
    ADD COLUMN bounds_min_lng REAL DEFAULT NULL;
ALTER TABLE radio_system_address_extraction_settings
    ADD COLUMN bounds_max_lng REAL DEFAULT NULL;

-- Seed initial bounding boxes (computed from existing geocoded calls + 0.1 padding)
UPDATE radio_system_address_extraction_settings
    SET bounds_min_lat = 44.90, bounds_max_lat = 46.30,
        bounds_min_lng = -78.40, bounds_max_lng = -76.20
    WHERE radio_system_id = 1;

UPDATE radio_system_address_extraction_settings
    SET bounds_min_lat = 44.30, bounds_max_lat = 45.20,
        bounds_min_lng = -77.20, bounds_max_lng = -76.15
    WHERE radio_system_id = 3;

UPDATE radio_system_address_extraction_settings
    SET bounds_min_lat = 45.30, bounds_max_lat = 46.40,
        bounds_min_lng = -78.30, bounds_max_lng = -76.50
    WHERE radio_system_id = 4;

COMMIT;
