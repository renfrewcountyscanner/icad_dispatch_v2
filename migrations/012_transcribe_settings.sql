-- 012_transcribe_settings.sql
-- Purpose: add new columns to radio_system_transcribe_settings (SQLite-safe redefine pattern)

BEGIN;
PRAGMA foreign_keys = OFF;

-- 0) Ensure baseline table exists (no-op if already there)
CREATE TABLE IF NOT EXISTS radio_system_transcribe_settings
(
    transcribe_setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id       INTEGER NOT NULL UNIQUE,
    transcribe_enabled    INTEGER DEFAULT 0,
    transcribe_url        TEXT,
    transcribe_api_key    TEXT,
    transcribe_config_id  INTEGER DEFAULT 1,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

-- 1) Create the new shape (old columns + your new ones)
CREATE TABLE radio_system_transcribe_settings__new
(
    transcribe_setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id       INTEGER NOT NULL UNIQUE,
    transcribe_enabled    INTEGER DEFAULT 0,
    transcribe_url        TEXT DEFAULT NULL,
    transcribe_api_key    TEXT DEFAULT NULL,
    transcribe_model      TEXT DEFAULT NULL,
    transcribe_language   TEXT DEFAULT NULL,
    transcribe_prompt     TEXT DEFAULT NULL,

    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

-- 2) Copy data forward (map existing columns; supply values for any NOT NULL additions)
INSERT INTO radio_system_transcribe_settings__new (
    transcribe_setting_id,
    radio_system_id,
    transcribe_enabled,
    transcribe_url,
    transcribe_api_key
)
SELECT
    transcribe_setting_id,
    radio_system_id,
    COALESCE(transcribe_enabled, 0),
    transcribe_url,
    transcribe_api_key

FROM radio_system_transcribe_settings;

-- 3) Swap tables
DROP TABLE radio_system_transcribe_settings;
ALTER TABLE radio_system_transcribe_settings__new RENAME TO radio_system_transcribe_settings;

-- 4) Recreate indexes / constraints as needed (keep UNIQUE on radio_system_id via schema above)
-- Optional explicit index (SQLite will still enforce UNIQUE, this can help lookups):
CREATE INDEX IF NOT EXISTS idx_rsts_radio_system_id
    ON radio_system_transcribe_settings (radio_system_id);

PRAGMA foreign_keys = ON;
COMMIT;
