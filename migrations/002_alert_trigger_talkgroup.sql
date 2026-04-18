-- Adds optional talk-group filter to alert_triggers
ALTER TABLE alert_triggers
    ADD COLUMN alert_trigger_talkgroup INTEGER DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_alert_triggers_tg
    ON alert_triggers (radio_system_id, alert_trigger_talkgroup);
