PRAGMA foreign_keys = ON;
BEGIN;

----------------------------------------------------------------------
-- 1) alert_triggers: rebuild to add pulsed fields and keep the
--    corrected name "alert_trigger_two_tone_a".
--    Copy ONLY rows whose radio_system_id exists.
----------------------------------------------------------------------

CREATE TABLE alert_triggers__new
(
    alert_trigger_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id                   INTEGER                                            NOT NULL,
    alert_trigger_name                TEXT                                                        DEFAULT NULL,

    -- normalize to AND/OR; default AND
    alert_trigger_type                TEXT CHECK ( alert_trigger_type IN ('AND','OR') ) NOT NULL DEFAULT 'AND',

    -- existing tone-based conditions (corrected A name)
    alert_trigger_two_tone_a          NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_two_tone_a_length   NUMERIC(6, 2)                                               DEFAULT 0.8,
    alert_trigger_two_tone_b          NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_two_tone_b_length   NUMERIC(6, 2)                                               DEFAULT 2.8,

    alert_trigger_long_tone           NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_long_tone_length    NUMERIC(6, 2)                                               DEFAULT 3.8,

    alert_trigger_hi_low_tone_a       NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_hi_low_tone_b       NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_hi_low_alternations INTEGER                                                     DEFAULT 4,

    -- per-trigger tolerance (percent)
    alert_trigger_tone_tolerance      NUMERIC(6, 2)                                               DEFAULT 2.0,

    -- throttle window (seconds)
    alert_trigger_ignore_time         NUMERIC(6, 2)                                               DEFAULT 300.0,

    -- optional talkgroup restriction
    alert_trigger_talkgroup           INTEGER                                                     DEFAULT NULL,

    -- delivery toggles / params
    alert_trigger_stream_url          TEXT                                                        DEFAULT NULL,
    alert_trigger_enable_discord      INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_make         INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_telegram     INTEGER                                                     DEFAULT 0,
    alert_trigger_enabled             INTEGER                                                     DEFAULT 1,

    -- NEW: pulsed single-tone (center Hz + min cycles)
    alert_trigger_pulsed_tone         NUMERIC(8, 2)                                               DEFAULT NULL,
    alert_trigger_pulsed_min_cycles   INTEGER                                                     DEFAULT 6,

    FOREIGN KEY (radio_system_id)
        REFERENCES radio_systems (radio_system_id)
        ON DELETE CASCADE
);

-- Copy + normalize; only rows with valid radio_system_id
INSERT INTO alert_triggers__new (
    alert_trigger_id,
    radio_system_id,
    alert_trigger_name,
    alert_trigger_type,
    alert_trigger_two_tone_a,
    alert_trigger_two_tone_a_length,
    alert_trigger_two_tone_b,
    alert_trigger_two_tone_b_length,
    alert_trigger_long_tone,
    alert_trigger_long_tone_length,
    alert_trigger_hi_low_tone_a,
    alert_trigger_hi_low_tone_b,
    alert_trigger_hi_low_alternations,
    alert_trigger_tone_tolerance,
    alert_trigger_ignore_time,
    alert_trigger_stream_url,
    alert_trigger_enable_discord,
    alert_trigger_enable_make,
    alert_trigger_enable_telegram,
    alert_trigger_enabled,
    alert_trigger_talkgroup
)
SELECT
    t.alert_trigger_id,
    t.radio_system_id,
    t.alert_trigger_name,

    -- Map legacy values to 'AND'/'OR' to satisfy CHECK constraint
    COALESCE(
            CASE LOWER(t.alert_trigger_type)
                WHEN 'and' THEN 'AND'
                WHEN 'or'  THEN 'OR'
                WHEN 'all' THEN 'AND'  -- legacy alias
                WHEN 'any' THEN 'OR'   -- legacy alias
                ELSE 'AND'
                END,
            'AND'
    ) AS alert_trigger_type,

    /* NOTE: earliest schema used "alert_two_tone_a"; preserve if present */
    t.alert_two_tone_a,
    t.alert_trigger_two_tone_a_length,
    t.alert_trigger_two_tone_b,
    t.alert_trigger_two_tone_b_length,

    t.alert_trigger_long_tone,
    t.alert_trigger_long_tone_length,

    t.alert_trigger_hi_low_tone_a,
    t.alert_trigger_hi_low_tone_b,
    t.alert_trigger_hi_low_alternations,

    t.alert_trigger_tone_tolerance,
    t.alert_trigger_ignore_time,

    t.alert_trigger_stream_url,
    t.alert_trigger_enable_discord,
    t.alert_trigger_enable_make,
    t.alert_trigger_enable_telegram,
    t.alert_trigger_enabled,
    t.alert_trigger_talkgroup
FROM alert_triggers AS t
WHERE EXISTS (
    SELECT 1 FROM radio_systems r
    WHERE r.radio_system_id = t.radio_system_id
);

DROP TABLE alert_triggers;
ALTER TABLE alert_triggers__new RENAME TO alert_triggers;

-- Helpful indexes (including pulsed center)
CREATE INDEX IF NOT EXISTS idx_alert_triggers_tg
    ON alert_triggers (radio_system_id, alert_trigger_talkgroup);

CREATE INDEX IF NOT EXISTS idx_alert_triggers_pulsed_tone
    ON alert_triggers (radio_system_id, alert_trigger_pulsed_tone);

----------------------------------------------------------------------
-- 2) call_tone_events: extend enum with 'pulsed'.
--    Copy ONLY rows whose call_id exists in call_records.
----------------------------------------------------------------------

CREATE TABLE call_tone_events__new
(
    tone_event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         INTEGER NOT NULL,

    tone_type       TEXT NOT NULL
        CHECK (tone_type IN ('two_tone','long','hi_low','mdc','dtmf','pulsed')),

    tone_set_id     TEXT NOT NULL,   -- qc_2 / lt_1 / hl_4 / pl_1
    json_payload    TEXT NOT NULL,

    -- flattened helpers (freq_a=center for pulsed)
    freq_a          REAL,
    freq_b          REAL,
    length_a_s      REAL,
    length_b_s      REAL,
    start_s         REAL,
    end_s           REAL,

    matches_trigger INTEGER NOT NULL DEFAULT 0
        CHECK (matches_trigger IN (0,1)),

    FOREIGN KEY (call_id) REFERENCES call_records (call_id) ON DELETE CASCADE
);

INSERT INTO call_tone_events__new (
    tone_event_id, call_id, tone_type, tone_set_id, json_payload,
    freq_a, freq_b, length_a_s, length_b_s, start_s, end_s, matches_trigger
)
SELECT
    cte.tone_event_id, cte.call_id, cte.tone_type, cte.tone_set_id, cte.json_payload,
    cte.freq_a, cte.freq_b, cte.length_a_s, cte.length_b_s, cte.start_s, cte.end_s, cte.matches_trigger
FROM call_tone_events AS cte
WHERE EXISTS (
    SELECT 1 FROM call_records cr
    WHERE cr.call_id = cte.call_id
);

DROP TABLE call_tone_events;
ALTER TABLE call_tone_events__new RENAME TO call_tone_events;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_call_tones_call
    ON call_tone_events (call_id);

CREATE INDEX IF NOT EXISTS idx_call_tones_type_freq
    ON call_tone_events (tone_type, freq_a, freq_b);

CREATE INDEX IF NOT EXISTS idx_call_tones_unmatched
    ON call_tone_events (matches_trigger);

COMMIT;
