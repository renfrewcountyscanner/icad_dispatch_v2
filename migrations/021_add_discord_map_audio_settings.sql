BEGIN;
PRAGMA foreign_keys = ON;

-- Add map render toggle
ALTER TABLE radio_system_discord_settings
ADD COLUMN discord_render_map INTEGER NOT NULL DEFAULT 0;

-- Add attach-audio toggle
ALTER TABLE radio_system_discord_settings
ADD COLUMN discord_attach_audio INTEGER NOT NULL DEFAULT 0;

COMMIT;
