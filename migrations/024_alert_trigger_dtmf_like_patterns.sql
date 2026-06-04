-- 024_alert_trigger_dtmf_like_patterns.sql
PRAGMA foreign_keys = OFF;
BEGIN;

-- Rebuild alert_trigger_dtmf_sequences:
-- - sequence now stores a LIKE pattern (supports % and _)
-- - drop match_type
-- - drop max_gap_ms

CREATE TABLE IF NOT EXISTS alert_trigger_dtmf_sequences_new
(
    dtmf_set_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id  INTEGER NOT NULL,
    rule_uid          TEXT    NOT NULL,
    sequence          TEXT    NOT NULL,         -- NOW: LIKE-style pattern
    sort_order        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE,
    CHECK (length(sequence) >= 1),
    UNIQUE (alert_trigger_id, rule_uid)
);

-- Copy + convert old rows (if table exists and has old columns).
-- NOTE: This assumes the old table exists (it does in your schema).
INSERT INTO alert_trigger_dtmf_sequences_new
(dtmf_set_id, alert_trigger_id, rule_uid, sequence, sort_order)
SELECT
    dtmf_set_id,
    alert_trigger_id,
    rule_uid,
    CASE match_type
        WHEN 'EXACT'    THEN sequence
        WHEN 'PREFIX'   THEN sequence || '%'
        WHEN 'CONTAINS' THEN '%' || sequence || '%'
        ELSE sequence
        END AS sequence,
    sort_order
FROM alert_trigger_dtmf_sequences;

DROP TABLE alert_trigger_dtmf_sequences;

ALTER TABLE alert_trigger_dtmf_sequences_new
    RENAME TO alert_trigger_dtmf_sequences;

-- Recreate indexes (updated for new shape)
CREATE INDEX IF NOT EXISTS idx_dtmf_sets_key
    ON alert_trigger_dtmf_sequences (alert_trigger_id, sequence);

CREATE INDEX IF NOT EXISTS idx_dtmf_sets_trigger
    ON alert_trigger_dtmf_sequences (alert_trigger_id, sort_order);

COMMIT;
PRAGMA foreign_keys = ON;
