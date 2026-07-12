BEGIN;

CREATE TABLE IF NOT EXISTS geocoding_address_aliases (
    address_alias_id SERIAL PRIMARY KEY,
    address_extraction_setting_id INTEGER NOT NULL
        REFERENCES radio_system_address_extraction_settings(address_extraction_setting_id)
            ON DELETE CASCADE,
    heard_phrase TEXT NOT NULL,
    canonical_address TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_epoch BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
    UNIQUE (address_extraction_setting_id, heard_phrase)
);

CREATE INDEX IF NOT EXISTS idx_geocoding_address_aliases_setting
    ON geocoding_address_aliases (address_extraction_setting_id, enabled);

ALTER TABLE radio_system_address_extraction_settings
    DROP CONSTRAINT IF EXISTS radio_system_address_extraction_settings_openai_model_check;
ALTER TABLE radio_system_address_extraction_settings
    ALTER COLUMN openai_model SET DEFAULT 'gpt-5.4-mini';
ALTER TABLE radio_system_address_extraction_settings
    ADD CONSTRAINT radio_system_address_extraction_settings_openai_model_check
    CHECK (openai_model IN ('gpt-5.4-mini', 'gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1'));
UPDATE radio_system_address_extraction_settings
    SET openai_model = 'gpt-5.4-mini'
    WHERE openai_model IN ('gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1');

ALTER TABLE radio_system_incident_classification_settings
    DROP CONSTRAINT IF EXISTS radio_system_incident_classification_settings_openai_model_check;
ALTER TABLE radio_system_incident_classification_settings
    ALTER COLUMN openai_model SET DEFAULT 'gpt-5.4-mini';
ALTER TABLE radio_system_incident_classification_settings
    ADD CONSTRAINT radio_system_incident_classification_settings_openai_model_check
    CHECK (openai_model IN ('gpt-5.4-mini', 'gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1'));
UPDATE radio_system_incident_classification_settings
    SET openai_model = 'gpt-5.4-mini'
    WHERE openai_model IN ('gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1');

INSERT INTO schema_migrations (version) VALUES (35)
    ON CONFLICT DO NOTHING;

COMMIT;
