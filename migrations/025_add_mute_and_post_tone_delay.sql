-- Migration 025: Add mute_notifications and post_tone_delay columns
-- Date: 2026-04-18

-- Add mute_notifications column to radio_systems table
ALTER TABLE radio_systems ADD COLUMN mute_notifications INTEGER DEFAULT 0;

-- Add post_tone_delay column to radio_systems table (seconds to skip after tone before recording)
ALTER TABLE radio_systems ADD COLUMN post_tone_delay INTEGER DEFAULT 0;
