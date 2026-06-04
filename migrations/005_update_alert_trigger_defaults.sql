-- 005_update_alert_trigger_defaults.sql
PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE alert_triggers__new
(
    alert_trigger_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id                   INTEGER                                            NOT NULL,
    alert_trigger_name                TEXT                                                        DEFAULT NULL,

    alert_trigger_type                TEXT CHECK ( alert_trigger_type IN ('AND','OR') ) NOT NULL DEFAULT 'AND',

    alert_trigger_two_tone_a          NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_two_tone_a_length   NUMERIC(6, 2)                                               DEFAULT 0.6,   -- updated
    alert_trigger_two_tone_b          NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_two_tone_b_length   NUMERIC(6, 2)                                               DEFAULT 2.3,   -- updated

    alert_trigger_long_tone           NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_long_tone_length    NUMERIC(6, 2)                                               DEFAULT 2.9,   -- updated

    alert_trigger_hi_low_tone_a       NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_hi_low_tone_b       NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_hi_low_alternations INTEGER                                                     DEFAULT 4,

    alert_trigger_tone_tolerance      NUMERIC(6, 2)                                               DEFAULT 2.0,

    alert_trigger_ignore_time         NUMERIC(6, 2)                                               DEFAULT 30.0,  -- updated

    alert_trigger_talkgroup           INTEGER                                                     DEFAULT NULL,

    alert_trigger_stream_url          TEXT                                                        DEFAULT NULL,
    alert_trigger_enable_discord      INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_make         INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_telegram     INTEGER                                                     DEFAULT 0,
    alert_trigger_enabled             INTEGER                                                     DEFAULT 1,

    alert_trigger_pulsed_tone         NUMERIC(8, 2)                                               DEFAULT NULL,
    alert_trigger_pulsed_min_cycles   INTEGER                                                     DEFAULT 6,

    FOREIGN KEY (radio_system_id)
        REFERENCES radio_systems (radio_system_id)
        ON DELETE CASCADE
);

INSERT INTO alert_triggers__new
SELECT *
FROM alert_triggers;

DROP TABLE alert_triggers;
ALTER TABLE alert_triggers__new RENAME TO alert_triggers;

CREATE INDEX IF NOT EXISTS idx_alert_triggers_tg
    ON alert_triggers (radio_system_id, alert_trigger_talkgroup);

CREATE INDEX IF NOT EXISTS idx_alert_triggers_pulsed_tone
    ON alert_triggers (radio_system_id, alert_trigger_pulsed_tone);

COMMIT;
