BEGIN;

-- One row per call's transcript (summary + raw, optional)
CREATE TABLE IF NOT EXISTS call_transcripts
(
    id         INTEGER PRIMARY KEY,
    call_id    INTEGER NOT NULL UNIQUE,    -- 1 transcript per call
    model      TEXT,
    language   TEXT,
    duration_s REAL,
    text_full  TEXT,                       -- assembled full text (for search/preview)
    has_words  INTEGER          DEFAULT 0, -- 1 if segments contained words[]
    raw_json   TEXT,                       -- optional: original provider response (for reprocessing)
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (call_id) REFERENCES call_records (call_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_call_transcripts_call
    ON call_transcripts (call_id);

-- One row per segment
CREATE TABLE IF NOT EXISTS call_transcript_segments
(
    id         INTEGER PRIMARY KEY,
    call_id    INTEGER NOT NULL,
    seg_index  INTEGER NOT NULL, -- as given by the provider (segments[i].id or index)
    start_s    REAL    NOT NULL,
    end_s      REAL    NOT NULL,
    text       TEXT    NOT NULL,
    word_count INTEGER          DEFAULT 0,
    words_json TEXT,             -- compact array: [{"start":..,"end":..,"word":"..."}]
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (call_id) REFERENCES call_records (call_id) ON DELETE CASCADE,
    UNIQUE (call_id, seg_index)
);

CREATE INDEX IF NOT EXISTS idx_call_transcript_segments_call
    ON call_transcript_segments (call_id);

CREATE INDEX IF NOT EXISTS idx_call_transcript_segments_time
    ON call_transcript_segments (call_id, start_s, end_s);

COMMIT;
