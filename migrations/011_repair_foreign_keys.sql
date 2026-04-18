-- 011_fix_remaining_child_fks_to_alert_triggers.sql
-- Rebuild remaining child tables so their FKs reference the REAL alert_triggers,
-- not the temporary alert_triggers__old (view).

PRAGMA foreign_keys = OFF;
BEGIN;

----------------------------------------------------------------------
-- OPTIONAL: clean obvious orphans before rebuild (safe with FKs OFF)
----------------------------------------------------------------------
DELETE FROM tone_trigger_map
WHERE alert_trigger_id NOT IN (SELECT alert_trigger_id FROM alert_triggers);

DELETE FROM trigger_fires
WHERE alert_trigger_id NOT IN (SELECT alert_trigger_id FROM alert_triggers);

DELETE FROM alert_trigger_last_fire
WHERE alert_trigger_id NOT IN (SELECT alert_trigger_id FROM alert_triggers);

----------------------------------------------------------------------
-- 1) trigger_fires
----------------------------------------------------------------------
CREATE TABLE trigger_fires__new
(
    trigger_fire_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id          INTEGER NOT NULL,
    alert_trigger_id INTEGER NOT NULL,
    fired_at_epoch_s INTEGER NOT NULL,

    FOREIGN KEY (call_id)
        REFERENCES call_records (call_id)
        ON DELETE CASCADE,

    FOREIGN KEY (alert_trigger_id)
        REFERENCES alert_triggers (alert_trigger_id)
        ON DELETE CASCADE
);

INSERT INTO trigger_fires__new (trigger_fire_id, call_id, alert_trigger_id, fired_at_epoch_s)
SELECT trigger_fire_id,       call_id, alert_trigger_id, fired_at_epoch_s
FROM trigger_fires;

DROP TABLE trigger_fires;
ALTER TABLE trigger_fires__new RENAME TO trigger_fires;

-- restore index
CREATE INDEX IF NOT EXISTS idx_trigger_fires_trigger_time
    ON trigger_fires (alert_trigger_id, fired_at_epoch_s DESC);

----------------------------------------------------------------------
-- 2) tone_trigger_map
----------------------------------------------------------------------
CREATE TABLE tone_trigger_map__new
(
    map_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tone_event_id    INTEGER NOT NULL,
    alert_trigger_id INTEGER NOT NULL,

    FOREIGN KEY (tone_event_id)
        REFERENCES call_tone_events (tone_event_id)
        ON DELETE CASCADE,

    FOREIGN KEY (alert_trigger_id)
        REFERENCES alert_triggers (alert_trigger_id)
        ON DELETE CASCADE,

    UNIQUE (tone_event_id, alert_trigger_id)
);

INSERT INTO tone_trigger_map__new (map_id, tone_event_id, alert_trigger_id)
SELECT map_id,                  tone_event_id, alert_trigger_id
FROM tone_trigger_map;

DROP TABLE tone_trigger_map;
ALTER TABLE tone_trigger_map__new RENAME TO tone_trigger_map;

-- restore helper index
CREATE INDEX IF NOT EXISTS idx_tone_trigger_by_trigger
    ON tone_trigger_map (alert_trigger_id);

----------------------------------------------------------------------
-- 3) alert_trigger_last_fire
----------------------------------------------------------------------
CREATE TABLE alert_trigger_last_fire__new
(
    alert_trigger_id INTEGER PRIMARY KEY,
    last_fired_at    INTEGER,
    FOREIGN KEY (alert_trigger_id)
        REFERENCES alert_triggers (alert_trigger_id)
        ON DELETE CASCADE
);

INSERT INTO alert_trigger_last_fire__new (alert_trigger_id, last_fired_at)
SELECT alert_trigger_id,              last_fired_at
FROM alert_trigger_last_fire;

DROP TABLE alert_trigger_last_fire;
ALTER TABLE alert_trigger_last_fire__new RENAME TO alert_trigger_last_fire;

COMMIT;
PRAGMA foreign_keys = ON;
