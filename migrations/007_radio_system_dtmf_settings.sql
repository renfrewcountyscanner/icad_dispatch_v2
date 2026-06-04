-- 007_radio_system_dtmf_settings.sql
PRAGMA foreign_keys = ON;
BEGIN;

-- Add DTMF controls to the per-system tone settings row
-- (SQLite allows ADD COLUMN; run once per DB)

ALTER TABLE radio_system_tone_settings
    ADD COLUMN dtmf_min_ms INTEGER NOT NULL DEFAULT 100
        CHECK (dtmf_min_ms BETWEEN 10 AND 2000);

ALTER TABLE radio_system_tone_settings
    ADD COLUMN dtmf_merge_ms INTEGER NOT NULL DEFAULT 75
        CHECK (dtmf_merge_ms BETWEEN 0 AND 1000);

ALTER TABLE radio_system_tone_settings
    ADD COLUMN dtmf_start_offset_ms INTEGER NOT NULL DEFAULT -20
        CHECK (dtmf_start_offset_ms BETWEEN -1000 AND 1000);

ALTER TABLE radio_system_tone_settings
    ADD COLUMN dtmf_end_offset_ms INTEGER NOT NULL DEFAULT 20
        CHECK (dtmf_end_offset_ms BETWEEN -1000 AND 1000);

COMMIT;
