PRAGMA foreign_keys = ON;
BEGIN;

-- TWO-TONE
CREATE TABLE IF NOT EXISTS alert_trigger_two_tone_sets
(
    two_tone_set_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id  INTEGER NOT NULL,
    rule_uid          TEXT    NOT NULL,           -- stable identity per row (client keeps it)
    freq_a_hz         REAL    NOT NULL,
    min_len_a_s       REAL    NOT NULL,
    freq_b_hz         REAL    NOT NULL,
    min_len_b_s       REAL    NOT NULL,
    tol_pct           REAL    DEFAULT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE,
    UNIQUE (alert_trigger_id, rule_uid),

    CHECK (freq_a_hz > 0 AND freq_b_hz > 0),
    CHECK (min_len_a_s >= 0 AND min_len_b_s >= 0),
    CHECK (tol_pct IS NULL OR (tol_pct >= 0 AND tol_pct <= 25))
);
-- performance indexes (formerly unique key columns)
CREATE INDEX IF NOT EXISTS idx_two_tone_sets_key
    ON alert_trigger_two_tone_sets (alert_trigger_id, freq_a_hz, freq_b_hz, min_len_a_s, min_len_b_s);
CREATE INDEX IF NOT EXISTS idx_two_tone_sets_trigger
    ON alert_trigger_two_tone_sets (alert_trigger_id, sort_order);

-- LONG TONE
CREATE TABLE IF NOT EXISTS alert_trigger_long_tone_sets
(
    long_tone_set_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id  INTEGER NOT NULL,
    rule_uid          TEXT    NOT NULL,
    freq_hz           REAL    NOT NULL,
    min_len_s         REAL    NOT NULL,
    tol_pct           REAL    DEFAULT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE,
    UNIQUE (alert_trigger_id, rule_uid),

    CHECK (freq_hz > 0),
    CHECK (min_len_s >= 0),
    CHECK (tol_pct IS NULL OR (tol_pct >= 0 AND tol_pct <= 25))
);
CREATE INDEX IF NOT EXISTS idx_long_tone_sets_key
    ON alert_trigger_long_tone_sets (alert_trigger_id, freq_hz, min_len_s);
CREATE INDEX IF NOT EXISTS idx_long_tone_sets_trigger
    ON alert_trigger_long_tone_sets (alert_trigger_id, sort_order);

-- HI/LOW
CREATE TABLE IF NOT EXISTS alert_trigger_hi_low_sets
(
    hi_low_set_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id  INTEGER NOT NULL,
    rule_uid          TEXT    NOT NULL,
    hi_freq_a_hz      REAL    NOT NULL,
    hi_freq_b_hz      REAL    NOT NULL,
    min_alternations  INTEGER NOT NULL DEFAULT 4,
    interval_s        REAL    DEFAULT NULL,
    tol_pct           REAL    DEFAULT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE,
    UNIQUE (alert_trigger_id, rule_uid),

    CHECK (hi_freq_a_hz > 0 AND hi_freq_b_hz > 0),
    CHECK (min_alternations >= 1),
    CHECK (interval_s IS NULL OR interval_s >= 0),
    CHECK (tol_pct IS NULL OR (tol_pct >= 0 AND tol_pct <= 25))
);
CREATE INDEX IF NOT EXISTS idx_hi_low_sets_key
    ON alert_trigger_hi_low_sets (alert_trigger_id, hi_freq_a_hz, hi_freq_b_hz, min_alternations, interval_s);
CREATE INDEX IF NOT EXISTS idx_hi_low_sets_trigger
    ON alert_trigger_hi_low_sets (alert_trigger_id, sort_order);

-- PULSED
CREATE TABLE IF NOT EXISTS alert_trigger_pulsed_sets
(
    pulsed_set_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id  INTEGER NOT NULL,
    rule_uid          TEXT    NOT NULL,
    center_hz         REAL    NOT NULL,
    min_cycles        INTEGER NOT NULL DEFAULT 6,
    min_on_ms         INTEGER DEFAULT NULL,
    max_on_ms         INTEGER DEFAULT NULL,
    min_off_ms        INTEGER DEFAULT NULL,
    max_off_ms        INTEGER DEFAULT NULL,
    tol_pct           REAL    DEFAULT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE,
    UNIQUE (alert_trigger_id, rule_uid),

    CHECK (center_hz > 0),
    CHECK (min_cycles >= 1),
    CHECK (min_on_ms IS NULL OR min_on_ms >= 0),
    CHECK (max_on_ms IS NULL OR max_on_ms >= 0),
    CHECK (min_off_ms IS NULL OR min_off_ms >= 0),
    CHECK (max_off_ms IS NULL OR max_off_ms >= 0),
    CHECK (tol_pct IS NULL OR (tol_pct >= 0 AND tol_pct <= 25))
);
CREATE INDEX IF NOT EXISTS idx_pulsed_sets_key
    ON alert_trigger_pulsed_sets (alert_trigger_id, center_hz, min_cycles, min_on_ms, max_on_ms, min_off_ms, max_off_ms);
CREATE INDEX IF NOT EXISTS idx_pulsed_sets_trigger
    ON alert_trigger_pulsed_sets (alert_trigger_id, sort_order);

-- DTMF (already pretty tight; keep content unique if you want dedupe)
CREATE TABLE IF NOT EXISTS alert_trigger_dtmf_sequences
(
    dtmf_set_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id  INTEGER NOT NULL,
    rule_uid          TEXT    NOT NULL,
    sequence          TEXT    NOT NULL,
    match_type        TEXT    NOT NULL DEFAULT 'EXACT'
        CHECK (match_type IN ('EXACT','PREFIX','CONTAINS')),
    max_gap_ms        INTEGER DEFAULT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE,
    CHECK (length(sequence) >= 1),
    UNIQUE (alert_trigger_id, rule_uid)
);
-- optional dedupe-by-content index (NOT unique)
CREATE INDEX IF NOT EXISTS idx_dtmf_sets_key
    ON alert_trigger_dtmf_sequences (alert_trigger_id, sequence, match_type);
CREATE INDEX IF NOT EXISTS idx_dtmf_sets_trigger
    ON alert_trigger_dtmf_sequences (alert_trigger_id, sort_order);

COMMIT;
