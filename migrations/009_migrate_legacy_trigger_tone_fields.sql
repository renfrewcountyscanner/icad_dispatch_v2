/* 009_migration_legacy_trigger_tone_fields.sql
   Purpose:
     - Backfill legacy one-off tone columns into new child-set tables
     - Rebuild alert_triggers WITHOUT legacy columns
     - Rebuild alert_trigger_pushover_settings to remove any leftover triggers
     - Create a compatibility VIEW alert_triggers__old to absorb stale references
*/

PRAGMA foreign_keys = ON;

/* ─────────────────────────────────────────────────────────────
   1) Backfill legacy one-off columns → new per-set tables
      (idempotent: per-trigger NOT EXISTS guard)
   ───────────────────────────────────────────────────────────── */
BEGIN;

-- TWO-TONE
INSERT INTO alert_trigger_two_tone_sets
(alert_trigger_id, rule_uid, freq_a_hz, min_len_a_s, freq_b_hz, min_len_b_s, tol_pct, sort_order)
SELECT
    at.alert_trigger_id,
    lower(hex(randomblob(8))) AS rule_uid,
    at.alert_trigger_two_tone_a,
    COALESCE(at.alert_trigger_two_tone_a_length, 0.6),
    at.alert_trigger_two_tone_b,
    COALESCE(at.alert_trigger_two_tone_b_length, 2.3),
    NULL,
    0
FROM alert_triggers at
WHERE at.alert_trigger_two_tone_a IS NOT NULL
  AND at.alert_trigger_two_tone_b IS NOT NULL
  AND at.alert_trigger_two_tone_a > 0
  AND at.alert_trigger_two_tone_b > 0
  AND NOT EXISTS (
    SELECT 1 FROM alert_trigger_two_tone_sets s
    WHERE s.alert_trigger_id = at.alert_trigger_id
);

-- LONG TONE
INSERT INTO alert_trigger_long_tone_sets
(alert_trigger_id, rule_uid, freq_hz, min_len_s, tol_pct, sort_order)
SELECT
    at.alert_trigger_id,
    lower(hex(randomblob(8))) AS rule_uid,
    at.alert_trigger_long_tone,
    COALESCE(at.alert_trigger_long_tone_length, 2.9),
    NULL,
    0
FROM alert_triggers at
WHERE at.alert_trigger_long_tone IS NOT NULL
  AND at.alert_trigger_long_tone > 0
  AND NOT EXISTS (
    SELECT 1 FROM alert_trigger_long_tone_sets s
    WHERE s.alert_trigger_id = at.alert_trigger_id
);

-- HI/LOW
INSERT INTO alert_trigger_hi_low_sets
(alert_trigger_id, rule_uid, hi_freq_a_hz, hi_freq_b_hz, min_alternations, interval_s, tol_pct, sort_order)
SELECT
    at.alert_trigger_id,
    lower(hex(randomblob(8))) AS rule_uid,
    at.alert_trigger_hi_low_tone_a,
    at.alert_trigger_hi_low_tone_b,
    COALESCE(at.alert_trigger_hi_low_alternations, 4),
    NULL,
    NULL,
    0
FROM alert_triggers at
WHERE at.alert_trigger_hi_low_tone_a IS NOT NULL
  AND at.alert_trigger_hi_low_tone_b IS NOT NULL
  AND at.alert_trigger_hi_low_tone_a > 0
  AND at.alert_trigger_hi_low_tone_b > 0
  AND NOT EXISTS (
    SELECT 1 FROM alert_trigger_hi_low_sets s
    WHERE s.alert_trigger_id = at.alert_trigger_id
);

-- PULSED
INSERT INTO alert_trigger_pulsed_sets
(alert_trigger_id, rule_uid, center_hz, min_cycles, min_on_ms, max_on_ms, min_off_ms, max_off_ms, tol_pct, sort_order)
SELECT
    at.alert_trigger_id,
    lower(hex(randomblob(8))) AS rule_uid,
    at.alert_trigger_pulsed_tone,
    COALESCE(at.alert_trigger_pulsed_min_cycles, 6),
    NULL, NULL, NULL, NULL,
    NULL,
    0
FROM alert_triggers at
WHERE at.alert_trigger_pulsed_tone IS NOT NULL
  AND at.alert_trigger_pulsed_tone > 0
  AND NOT EXISTS (
    SELECT 1 FROM alert_trigger_pulsed_sets s
    WHERE s.alert_trigger_id = at.alert_trigger_id
);

COMMIT;

/* ─────────────────────────────────────────────────────────────
   2) Rebuild alert_triggers WITHOUT legacy columns
      (physically drop those columns)
   ───────────────────────────────────────────────────────────── */
PRAGMA foreign_keys = OFF;
BEGIN;

ALTER TABLE alert_triggers RENAME TO alert_triggers__old;

CREATE TABLE alert_triggers
(
    alert_trigger_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id               INTEGER                                            NOT NULL,
    alert_trigger_name            TEXT                                                        DEFAULT NULL,
    alert_trigger_type            TEXT CHECK ( alert_trigger_type IN ('AND','OR') ) NOT NULL DEFAULT 'AND',
    alert_trigger_tone_tolerance  NUMERIC(6, 2)                                               DEFAULT 2.0,
    alert_trigger_ignore_time     NUMERIC(6, 2)                                               DEFAULT 30.0,
    alert_trigger_talkgroup       INTEGER                                                     DEFAULT NULL,
    alert_trigger_stream_url      TEXT                                                        DEFAULT NULL,
    alert_trigger_enable_discord  INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_make     INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_telegram INTEGER                                                     DEFAULT 0,
    alert_trigger_enabled         INTEGER                                                     DEFAULT 1,
    FOREIGN KEY (radio_system_id)
        REFERENCES radio_systems (radio_system_id)
        ON DELETE CASCADE
);

INSERT INTO alert_triggers (
    alert_trigger_id,
    radio_system_id,
    alert_trigger_name,
    alert_trigger_type,
    alert_trigger_tone_tolerance,
    alert_trigger_ignore_time,
    alert_trigger_talkgroup,
    alert_trigger_stream_url,
    alert_trigger_enable_discord,
    alert_trigger_enable_make,
    alert_trigger_enable_telegram,
    alert_trigger_enabled
)
SELECT
    alert_trigger_id,
    radio_system_id,
    alert_trigger_name,
    alert_trigger_type,
    alert_trigger_tone_tolerance,
    alert_trigger_ignore_time,
    alert_trigger_talkgroup,
    alert_trigger_stream_url,
    alert_trigger_enable_discord,
    alert_trigger_enable_make,
    alert_trigger_enable_telegram,
    alert_trigger_enabled
FROM alert_triggers__old;

DROP TABLE alert_triggers__old;

CREATE INDEX IF NOT EXISTS idx_alert_triggers_tg
    ON alert_triggers (radio_system_id, alert_trigger_talkgroup);

COMMIT;
PRAGMA foreign_keys = ON;

/* ─────────────────────────────────────────────────────────────
   3) Rebuild alert_trigger_pushover_settings to nuke stale triggers
      (copy → recreate → copy)
   ───────────────────────────────────────────────────────────── */
PRAGMA foreign_keys = OFF;
BEGIN;

-- If it doesn't exist yet, create empty with the right schema
CREATE TABLE IF NOT EXISTS alert_trigger_pushover_settings (
                                                               alert_trigger_id      INTEGER PRIMARY KEY
                                                                   REFERENCES alert_triggers(alert_trigger_id) ON DELETE CASCADE,
                                                               enable_pushover       INTEGER DEFAULT 0,
                                                               pushover_group_token  TEXT,
                                                               pushover_app_token    TEXT,
                                                               pushover_body         TEXT,
                                                               pushover_subject      TEXT,
                                                               pushover_sound        TEXT
);

-- Recreate table to guarantee no attached BEFORE/AFTER triggers remain
ALTER TABLE alert_trigger_pushover_settings
    RENAME TO alert_trigger_pushover_settings__tmp;

CREATE TABLE alert_trigger_pushover_settings (
                                                 alert_trigger_id      INTEGER PRIMARY KEY
                                                     REFERENCES alert_triggers(alert_trigger_id) ON DELETE CASCADE,
                                                 enable_pushover       INTEGER DEFAULT 0,
                                                 pushover_group_token  TEXT,
                                                 pushover_app_token    TEXT,
                                                 pushover_body         TEXT,
                                                 pushover_subject      TEXT,
                                                 pushover_sound        TEXT
);

INSERT INTO alert_trigger_pushover_settings
(alert_trigger_id, enable_pushover, pushover_group_token, pushover_app_token,
 pushover_body, pushover_subject, pushover_sound)
SELECT
    alert_trigger_id, enable_pushover, pushover_group_token, pushover_app_token,
    pushover_body, pushover_subject, pushover_sound
FROM alert_trigger_pushover_settings__tmp;

DROP TABLE alert_trigger_pushover_settings__tmp;

COMMIT;
PRAGMA foreign_keys = ON;

/* ─────────────────────────────────────────────────────────────
   4) Create a read-only compatibility VIEW named alert_triggers__old
      so any lingering SELECTs continue to work.
      Legacy tone columns are projected as NULLs.
   ───────────────────────────────────────────────────────────── */
BEGIN;

DROP VIEW IF EXISTS alert_triggers__old;

CREATE VIEW alert_triggers__old AS
SELECT
    alert_trigger_id,
    radio_system_id,
    alert_trigger_name,
    alert_trigger_type,
    /* legacy flat tone columns (now migrated to child tables): */
    NULL AS alert_trigger_two_tone_a,
    NULL AS alert_trigger_two_tone_a_length,
    NULL AS alert_trigger_two_tone_b,
    NULL AS alert_trigger_two_tone_b_length,
    NULL AS alert_trigger_long_tone,
    NULL AS alert_trigger_long_tone_length,
    NULL AS alert_trigger_hi_low_tone_a,
    NULL AS alert_trigger_hi_low_tone_b,
    NULL AS alert_trigger_hi_low_alternations,
    /* retained columns map 1:1: */
    alert_trigger_tone_tolerance,
    alert_trigger_ignore_time,
    alert_trigger_talkgroup,
    alert_trigger_stream_url,
    alert_trigger_enable_discord,
    alert_trigger_enable_make,
    alert_trigger_enable_telegram,
    alert_trigger_enabled,
    /* more legacy: */
    NULL AS alert_trigger_pulsed_tone,
    NULL AS alert_trigger_pulsed_min_cycles
FROM alert_triggers;

COMMIT;

/* optional */
VACUUM;
