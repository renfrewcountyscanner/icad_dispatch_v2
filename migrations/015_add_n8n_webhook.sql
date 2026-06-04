PRAGMA foreign_keys = ON;
BEGIN;

-- per-system n8n config (webhook + HS256 passphrase)
CREATE TABLE IF NOT EXISTS radio_system_n8n_settings
(
    n8n_setting_id       INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 1:1 with radio_systems
    radio_system_id      INTEGER NOT NULL UNIQUE
        REFERENCES radio_systems (radio_system_id)
            ON DELETE CASCADE,

    -- master switch
    n8n_enabled          INTEGER NOT NULL DEFAULT 0
        CHECK (n8n_enabled IN (0,1)),

    -- webhook target (required) + timeout
    n8n_webhook_url      TEXT    DEFAULT NULL,
    n8n_timeout_s        INTEGER NOT NULL DEFAULT 10,

    -- JWT (HS256) using a single passphrase
    jwt_passphrase       TEXT    DEFAULT NULL,                -- shared secret
    jwt_issuer           TEXT    DEFAULT 'icad_dispatch',
    jwt_audience         TEXT    DEFAULT 'n8n',
    jwt_subject_template TEXT    DEFAULT '{radio_system_id}',
    jwt_ttl_s            INTEGER NOT NULL DEFAULT 300     -- 5 minutes
);

CREATE INDEX IF NOT EXISTS idx_rsns_radio_system_id
    ON radio_system_n8n_settings (radio_system_id);

CREATE INDEX IF NOT EXISTS idx_rsns_enabled
    ON radio_system_n8n_settings (n8n_enabled);

-- (optional) per-trigger gate
ALTER TABLE alert_triggers
    ADD COLUMN alert_trigger_enable_n8n INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_alert_triggers_enable_n8n
    ON alert_triggers (radio_system_id, alert_trigger_enable_n8n);

COMMIT;
