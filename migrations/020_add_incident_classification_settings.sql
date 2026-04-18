BEGIN;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS radio_system_incident_classification_settings
(
    incident_classification_setting_id  INTEGER PRIMARY KEY AUTOINCREMENT,

    radio_system_id INTEGER NOT NULL UNIQUE
        REFERENCES radio_systems (radio_system_id)
            ON DELETE CASCADE,

    incident_classification_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (incident_classification_enabled IN (0,1)),

    -- Optional overrides (only used when use_address_extraction_openai=0)
    openai_api_key TEXT DEFAULT NULL,
    openai_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'
        CHECK (openai_model IN ('gpt-4o-mini','gpt-4o','gpt-4.1-mini','gpt-4.1')),

    min_confidence REAL NOT NULL DEFAULT 0.0
        CHECK (min_confidence >= 0.0 AND min_confidence <= 1.0)

);

CREATE INDEX IF NOT EXISTS idx_rsics_radio_system_id
    ON radio_system_incident_classification_settings (radio_system_id);

CREATE INDEX IF NOT EXISTS idx_rsics_enabled
    ON radio_system_incident_classification_settings (incident_classification_enabled);

COMMIT;
