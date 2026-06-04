CREATE TABLE IF NOT EXISTS radio_system_ntfy_settings (
    ntfy_setting_id   SERIAL PRIMARY KEY,
    radio_system_id   INTEGER NOT NULL UNIQUE REFERENCES radio_systems ON DELETE CASCADE,
    ntfy_enabled      INTEGER DEFAULT 0,
    ntfy_server_url   TEXT    DEFAULT 'https://ntfy.sh',
    ntfy_topic        TEXT    DEFAULT '',
    ntfy_token        TEXT    DEFAULT '',
    ntfy_title_tmpl   TEXT    DEFAULT '{system_name} • {trigger_list}',
    ntfy_body_tmpl    TEXT    DEFAULT '{transcript}\n\n{audio_url}'
);

ALTER TABLE alert_triggers
    ADD COLUMN IF NOT EXISTS alert_trigger_enable_ntfy  INTEGER DEFAULT 0;

ALTER TABLE alert_triggers
    ADD COLUMN IF NOT EXISTS alert_trigger_ntfy_topic     TEXT    DEFAULT '';

INSERT INTO schema_migrations (version) VALUES (32)
    ON CONFLICT DO NOTHING;
