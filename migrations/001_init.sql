-- Always turn this on (once per connection)
PRAGMA
foreign_keys = ON;

BEGIN;

-------------------------------------------------
-- USERS / AUTH
-------------------------------------------------
CREATE TABLE IF NOT EXISTS users
(
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_username TEXT NOT NULL UNIQUE,
    user_password TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_remember_tokens
(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    series     TEXT    NOT NULL,
    token_hash TEXT    NOT NULL,
    issued_at  DATETIME DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    UNIQUE (user_id, series) -- no duplicate series per user
);
CREATE INDEX IF NOT EXISTS idx_user_remember_tokens_series ON user_remember_tokens (series);
CREATE INDEX IF NOT EXISTS idx_user_remember_tokens_user_id ON user_remember_tokens (user_id);

-------------------------------------------------
-- RADIO SYSTEMS
-------------------------------------------------
CREATE TABLE IF NOT EXISTS radio_systems
(
    radio_system_id INTEGER PRIMARY KEY AUTOINCREMENT,
    system_decimal  INTEGER NOT NULL UNIQUE,
    system_name     TEXT,
    stream_url      TEXT,
    api_key         TEXT
);

/* ────────────────────────────────────────────────────────────────
   RADIO-SYSTEM UPLOAD SETTINGS  –  tone-only / split-dispatch control
   ──────────────────────────────────────────────────────────────── */
CREATE TABLE IF NOT EXISTS radio_system_upload_settings
(
    /* 1-to-1 relationship with radio_systems -------------------- */
    radio_system_id      INTEGER PRIMARY KEY
        REFERENCES radio_systems (radio_system_id)
            ON DELETE CASCADE,

    /* === master switch ========================================= */
    split_enabled        INTEGER NOT NULL DEFAULT 0
        CHECK (split_enabled IN (0, 1)),
    -- 0 = ignore stub logic, 1 = enable

    /* === tone-tail / voice detection knobs ===================== */
    tail_min_voice_sec   REAL    NOT NULL DEFAULT 3.0,
    -- If the audio that follows the last detected tone is
    -- < this many seconds, treat the file as “tone-only stub”.

    vad_min_speech_ratio REAL    NOT NULL DEFAULT 0.15,
    -- Optional second test (0–1).  After tones are stripped,
    -- run a quick energy VAD on the *tail*; if the percentage
    -- of speechy frames ≥ this value, accept as voice.

    voice_rms_dbfs       REAL    NOT NULL DEFAULT -35.0,
    -- RMS threshold (dBFS) used by the light-weight VAD.  Lower
    -- = more sensitive; raise in noisy systems.

    /* === merge window controls ================================= */
    max_split_interval   REAL    NOT NULL DEFAULT 30.0,
    -- Seconds; if a stub is older than this when the next clip
    -- arrives it is discarded.

    max_split_length     REAL    NOT NULL DEFAULT 30.0
    -- If an incoming clip is shorter than this *and* qualifies
    -- as “tone-only”, we store it as a stub instead of uploading
    -- immediately.  (Prevents 1-second stubs from piling up.)
);

/* helpful indexes */
CREATE INDEX IF NOT EXISTS idx_upload_cfg_split_enabled ON radio_system_upload_settings (split_enabled);

-------------------------------------------------
-- 1:1 SETTINGS TABLES (one row per system)
-------------------------------------------------
CREATE TABLE IF NOT EXISTS radio_system_tone_settings
(
    tone_settings_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id         INTEGER NOT NULL UNIQUE,
    tone_finder_enabled     INTEGER       DEFAULT 0,
    matching_threshold      NUMERIC(6, 2) DEFAULT 2.0,
    tone_a_min_length       NUMERIC(6, 2) DEFAULT 0.7,
    tone_b_min_length       NUMERIC(6, 2) DEFAULT 2.6,
    hi_low_interval         NUMERIC(6, 2) DEFAULT 0.2,
    hi_low_min_alternations INTEGER       DEFAULT 6,
    long_tone_min_length    NUMERIC(6, 2) DEFAULT 1.8,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS radio_system_email_settings
(
    email_setting_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id     INTEGER NOT NULL UNIQUE,
    email_enabled       INTEGER DEFAULT 0,
    smtp_hostname       TEXT,
    smtp_port           INTEGER,
    smtp_username       TEXT,
    smtp_password       TEXT,
    email_address_from  TEXT    DEFAULT 'dispatch@example.com',
    email_text_from     TEXT    DEFAULT 'iCAD Dispatch',
    email_alert_subject TEXT    DEFAULT 'Dispatch Alert',
    email_alert_body    TEXT    DEFAULT '{trigger_list} Alert at {timestamp_24}<br><br>{transcript}<br><br><a href="{audio_url}">Click for Dispatch Audio</a><br><br><a href="{stream_url}">Click Audio Stream</a>',
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS radio_system_pushover_settings
(
    pushover_setting_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id      INTEGER NOT NULL UNIQUE,
    pushover_enabled     INTEGER DEFAULT 0,
    pushover_group_token TEXT,
    pushover_app_token   TEXT,
    pushover_body        TEXT    DEFAULT '<b>{system_name} — {trigger_list}</b>\n<i>{incident_category} • {incident_type}</i>\n\n{address}\n\n{transcript}\n\n<a href="{audio_url}">Dispatch Audio</a>\n<small>{timestamp_12} • Call #{call_id}</small>',
    pushover_subject     TEXT    DEFAULT '{system_name} — {trigger_list}',
    pushover_sound       TEXT    DEFAULT 'pushover',
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS radio_system_telegram_settings
(
    telegram_setting_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id       INTEGER NOT NULL UNIQUE,
    telegram_enabled      INTEGER DEFAULT 0,
    telegram_bot_token    TEXT,
    telegram_channel_id   TEXT,
    telegram_message_body TEXT    DEFAULT '{timestamp}\n{trigger_list}\n{transcript}\niCAD Dispatch',
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS radio_system_discord_settings
(
    discord_setting_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id      INTEGER NOT NULL UNIQUE,
    discord_enabled      INTEGER DEFAULT 0,
    discord_webhook_url  TEXT,
    discord_embed_title  TEXT,
    discord_embed_color  TEXT,
    discord_embed_footer TEXT,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS radio_system_discord_embed_fields
(
    embed_field_id     INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Parent settings row
    discord_setting_id INTEGER NOT NULL,

    -- Short stable identifier you use in code when updating a specific field
    field_key          TEXT    NOT NULL,

    -- What Discord shows as the field "name" (label)
    field_label        TEXT    NOT NULL,

    -- Template / literal string that becomes the field "value"
    -- (You can store tokens like {{talkgroup_name}} to be expanded in app code.)
    field_template     TEXT    NOT NULL,

    -- Discord embed 'inline' property: 1 = inline, 0 = full-width
    field_inline       INTEGER NOT NULL DEFAULT 0,

    -- Allow user to turn a field on/off without deleting it
    field_enabled      INTEGER NOT NULL DEFAULT 1,

    -- Display order inside the embed (lower comes first)
    sort_order         INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (discord_setting_id)
        REFERENCES radio_system_discord_settings (discord_setting_id)
        ON DELETE CASCADE,

    -- No duplicate keys within the same settings object
    UNIQUE (discord_setting_id, field_key),

    -- Optional: enforce Discord limits (can omit if you prefer to validate in code)
    CHECK (length(field_label) <= 256),
    CHECK (length(field_template) <= 1024),
    CHECK (field_inline IN (0, 1)),
    CHECK (field_enabled IN (0, 1))
);

-- index for discord fields
CREATE INDEX IF NOT EXISTS idx_discord_embed_fields_parent_order
    ON radio_system_discord_embed_fields (discord_setting_id, sort_order);

CREATE TABLE IF NOT EXISTS radio_system_make_settings
(
    make_setting_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id  INTEGER NOT NULL UNIQUE,
    make_enabled     INTEGER DEFAULT 0,
    make_webhook_url TEXT,
    make_api_key     TEXT,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

-- ─────────────────────────────────────────────────────────────
--  radio_system_make_payload_fields
--  (1‑to‑many with radio_system_make_settings)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS radio_system_make_payload_fields
(
    payload_field_id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- parent row (unique per system) ---------------------------------
    make_setting_id  INTEGER NOT NULL,

    -- user‑defined JSON key sent to Make.com
    field_key        TEXT    NOT NULL,

    -- the literal or tokenised value you’ll expand in code, e.g.
    --   "Lexington Dispatch"     (static)
    --   "{trigger_list}"         (token)
    --   "{timestamp_24} {mp3_url}" (compound)
    field_value      TEXT    NOT NULL,

    -- allow quick on/off without deleting record
    field_enabled    INTEGER NOT NULL DEFAULT 1,

    ------------------------------------------------------------------
    -- keep each key unique *within that one Make settings row*
    UNIQUE (make_setting_id, field_key),

    -- enforce a few safety limits (optional)
    CHECK (length(field_key) <= 256),
    CHECK (length(field_value) <= 1024),
    CHECK (field_enabled IN (0, 1)),

    FOREIGN KEY (make_setting_id)
        REFERENCES radio_system_make_settings (make_setting_id)
        ON DELETE CASCADE
);

-- Helpful index when you fetch all fields for one system
CREATE INDEX IF NOT EXISTS idx_make_payload_fields_parent
    ON radio_system_make_payload_fields (make_setting_id);

-------------------------------------------------
-- PER-SYSTEM EMAIL RECIPIENT LIST (1:N)
-------------------------------------------------
CREATE TABLE IF NOT EXISTS radio_system_emails
(
    email_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id INTEGER NOT NULL,
    email_address   TEXT    NOT NULL,
    enabled         INTEGER DEFAULT 1,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_system_emails_radio_id ON radio_system_emails (radio_system_id);

-------------------------------------------------
-- PER-SYSTEM Transcribe Settings
-------------------------------------------------
CREATE TABLE IF NOT EXISTS radio_system_transcribe_settings
(
    transcribe_setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id       INTEGER NOT NULL UNIQUE,
    transcribe_enabled    INTEGER DEFAULT 0,
    transcribe_url        TEXT,
    transcribe_api_key    TEXT,
    transcribe_config_id  INTEGER DEFAULT 1,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

-------------------------------------------------
-- Alert Triggers
-------------------------------------------------

CREATE TABLE IF NOT EXISTS alert_triggers
(
    alert_trigger_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id                   INTEGER                                            NOT NULL,
    alert_trigger_name                TEXT                                                        DEFAULT NULL,
    alert_trigger_type                TEXT CHECK ( alert_trigger_type IN ('AND', 'OR') ) NOT NULL DEFAULT 'AND',
    alert_two_tone_a                  NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_two_tone_a_length   NUMERIC(6, 2)                                               DEFAULT 0.8,
    alert_trigger_two_tone_b          NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_two_tone_b_length   NUMERIC(6, 2)                                               DEFAULT 2.8,
    alert_trigger_long_tone           NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_long_tone_length    NUMERIC(6, 2)                                               DEFAULT 3.8,
    alert_trigger_hi_low_tone_a       NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_hi_low_tone_b       NUMERIC(6, 2)                                               DEFAULT NULL,
    alert_trigger_hi_low_alternations INTEGER                                                     DEFAULT 4,
    alert_trigger_tone_tolerance      NUMERIC(6, 2)                                               DEFAULT 2.0,
    alert_trigger_ignore_time         NUMERIC(6, 2)                                               DEFAULT 300.0,
    alert_trigger_stream_url          TEXT                                                        DEFAULT NULL,
    alert_trigger_enable_discord      INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_make         INTEGER                                                     DEFAULT 0,
    alert_trigger_enable_telegram     INTEGER                                                     DEFAULT 0,
    alert_trigger_enabled             INTEGER                                                     DEFAULT 1,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alert_trigger_pushover_settings
(
    alert_trigger_pushover_setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_trigger_id                  INTEGER NOT NULL UNIQUE,
    enable_pushover                   INTEGER DEFAULT 0,
    pushover_group_token              TEXT    DEFAULT NULL,
    pushover_app_token                TEXT    DEFAULT NULL,
    pushover_body                     TEXT    DEFAULT '<b>{system_name} — {trigger_list}</b>\n<i>{incident_category} • {incident_type}</i>\n\n{address}\n\n{transcript}\n\n<a href="{audio_url}">Dispatch Audio</a>\n<small>{timestamp_12} • Call #{call_id}</small>',
    pushover_subject                  TEXT    DEFAULT '{system_name} — {trigger_list}',
    pushover_sound                    TEXT    DEFAULT 'pushover',
    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alert_trigger_last_fire
(
    alert_trigger_id INTEGER PRIMARY KEY,
    last_fired_at    INTEGER,
    FOREIGN KEY (alert_trigger_id) REFERENCES alert_triggers (alert_trigger_id) ON DELETE CASCADE
);

/* ─────────────────────────────────────────────────────────────
   1.  CALL-LEVEL METADATA
   ───────────────────────────────────────────────────────────── */
CREATE TABLE IF NOT EXISTS call_records
(
    call_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    radio_system_id  INTEGER NOT NULL,
    start_epoch_s    INTEGER NOT NULL,        -- Unix seconds
    duration_s       REAL    NOT NULL,
    talkgroup        INTEGER,
    merged_from_stub INTEGER DEFAULT 0,       -- 1 = yes
    file_path        TEXT    NOT NULL,        -- relative path under /static
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),

    FOREIGN KEY (radio_system_id)
    REFERENCES radio_systems (radio_system_id)
    ON DELETE CASCADE
    );

CREATE INDEX IF NOT EXISTS idx_call_records_system_time
    ON call_records (radio_system_id, start_epoch_s DESC);

/* ─────────────────────────────────────────────────────────────
   2.  PER-TONE-SET EVENTS
   ───────────────────────────────────────────────────────────── */
CREATE TABLE IF NOT EXISTS call_tone_events
(
    tone_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id       INTEGER NOT NULL,
    tone_type     TEXT    NOT NULL        -- two_tone | long | hi_low | mdc | dtmf
    CHECK (tone_type IN ('two_tone','long','hi_low','mdc','dtmf')),

    tone_set_id   TEXT    NOT NULL,       -- qc_2 / lt_1 / hl_4  (stable inside call)

    json_payload  TEXT    NOT NULL,       -- full dict from detector

-- flattened helpers (nullable)
    freq_a        REAL,
    freq_b        REAL,
    length_a_s    REAL,
    length_b_s    REAL,
    start_s       REAL,
    end_s         REAL,

    matches_trigger INTEGER NOT NULL DEFAULT 0  -- 1 = at least one trigger used this
    CHECK (matches_trigger IN (0,1)),

    FOREIGN KEY (call_id)
    REFERENCES call_records (call_id)
    ON DELETE CASCADE
    );

CREATE INDEX IF NOT EXISTS idx_call_tones_call          ON call_tone_events (call_id);
CREATE INDEX IF NOT EXISTS idx_call_tones_type_freq     ON call_tone_events (tone_type,freq_a,freq_b);
CREATE INDEX IF NOT EXISTS idx_call_tones_unmatched     ON call_tone_events (matches_trigger);

/* ─────────────────────────────────────────────────────────────
   3.  TRIGGER-LEVEL FIRE HISTORY (call granularity)
   ───────────────────────────────────────────────────────────── */
CREATE TABLE IF NOT EXISTS trigger_fires
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

CREATE INDEX IF NOT EXISTS idx_trigger_fires_trigger_time
    ON trigger_fires (alert_trigger_id, fired_at_epoch_s DESC);

/* ─────────────────────────────────────────────────────────────
   4.  TONE-SET ⇄ TRIGGER MAPPING (many-to-many)
   ───────────────────────────────────────────────────────────── */
CREATE TABLE IF NOT EXISTS tone_trigger_map
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

CREATE INDEX IF NOT EXISTS idx_tone_trigger_by_trigger
    ON tone_trigger_map (alert_trigger_id);

COMMIT;
