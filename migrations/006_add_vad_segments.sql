-- XXX_add_vad_segments.sql
-- Migration: Add call_vad_segments table to store pre-computed VAD segments
-- Safe to run multiple times (IF NOT EXISTS on table + index creation)

BEGIN;

CREATE TABLE IF NOT EXISTS call_vad_segments
(
    id      INTEGER PRIMARY KEY,
    call_id INTEGER                                   NOT NULL,
    kind    TEXT CHECK (kind IN ('voice', 'silence')) NOT NULL,
    start_s REAL                                      NOT NULL,
    end_s   REAL                                      NOT NULL,
    FOREIGN KEY (call_id) REFERENCES call_records (call_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_call_vad_segments_call
    ON call_vad_segments (call_id);

COMMIT;
