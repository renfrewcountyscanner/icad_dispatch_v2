BEGIN;
PRAGMA foreign_keys = ON;

-- Add audio minimum length
ALTER TABLE radio_system_upload_settings
ADD COLUMN audio_min_length INTEGER NOT NULL DEFAULT 2.0;

COMMIT;
