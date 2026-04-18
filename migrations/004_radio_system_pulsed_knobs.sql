-- 004_radio_system_pulsed_knobs.sql
PRAGMA foreign_keys = ON;
BEGIN;

-- Add high-level pulsed single-tone knobs to radio_system_tone_settings.
-- (SQLite allows ADD COLUMN; safe to run once per DB.)
ALTER TABLE radio_system_tone_settings
    ADD COLUMN pulsed_min_cycles    INTEGER       DEFAULT 6;

ALTER TABLE radio_system_tone_settings
    ADD COLUMN pulsed_min_on_ms     INTEGER       DEFAULT 120;

ALTER TABLE radio_system_tone_settings
    ADD COLUMN pulsed_max_on_ms     INTEGER       DEFAULT 900;

ALTER TABLE radio_system_tone_settings
    ADD COLUMN pulsed_min_off_ms    INTEGER       DEFAULT 25;

ALTER TABLE radio_system_tone_settings
    ADD COLUMN pulsed_max_off_ms    INTEGER       DEFAULT 350;

COMMIT;
