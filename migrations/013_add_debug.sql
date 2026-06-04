BEGIN;
PRAGMA foreign_keys = OFF;

-- Add a nullable debug_url to hold the muted+normalized debug audio URL.
ALTER TABLE call_records
    ADD COLUMN debug_file_path TEXT;

PRAGMA foreign_keys = ON;
COMMIT;
