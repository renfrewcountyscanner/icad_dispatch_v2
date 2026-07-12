PRAGMA foreign_keys = ON;
BEGIN;

ALTER TABLE radio_system_address_extraction_settings
    ADD COLUMN nominatim_base_url TEXT DEFAULT NULL;

INSERT INTO schema_migrations (version) VALUES (33)
    ON CONFLICT DO NOTHING;

COMMIT;
