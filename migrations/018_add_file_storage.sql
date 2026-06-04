-- 018_storage_backends.sql

BEGIN;

PRAGMA
foreign_keys = ON;

-- -----------------------------------------------
-- RADIO SYSTEM STORAGE SETTINGS
-- -----------------------------------------------

CREATE TABLE radio_system_storage_settings
(
    -- 1:1 with radio_systems
    radio_system_id INTEGER PRIMARY KEY
        REFERENCES radio_systems (radio_system_id)
            ON DELETE CASCADE,

    -- Which storage backend this system uses
    storage_type TEXT NOT NULL DEFAULT 'LOCAL'
        CHECK (storage_type IN ('LOCAL','SFTP','S3')),

    -- Optional pattern if you ever want to override the default
    -- Effective path: <remote_path or object_key_prefix> || '/' || strftime(path_pattern)
    path_pattern TEXT NOT NULL DEFAULT '%Y/%m/%d',

    -- ============================
    -- SFTP / SCP OPTIONS
    -- ============================
    sftp_host        TEXT,
    sftp_port        INTEGER NOT NULL DEFAULT 22,
    sftp_username    TEXT,
    sftp_password    TEXT,
    sftp_use_ssh_key INTEGER NOT NULL DEFAULT 0,
    sftp_remote_path TEXT,    -- base folder; e.g. /var/www/audio
    sftp_base_url    TEXT,    -- e.g. https://audio.example.com
    sftp_timeout_s   INTEGER NOT NULL DEFAULT 30,
    sftp_max_retries INTEGER NOT NULL DEFAULT 3,

    -- ============================
    -- S3 / S3-COMPAT OPTIONS
    -- ============================
    s3_bucket            TEXT,
    s3_region            TEXT,   -- e.g. us-east-1
    s3_access_key_id     TEXT,
    s3_secret_access_key TEXT,
    s3_endpoint_url      TEXT,   -- Minio/Wasabi endpoint; NULL for AWS default
    s3_object_key_prefix TEXT,   -- e.g. "icad/audio" (prepended before %Y/%m/%d/file.mp3)

    -- Basic integrity checks so you don’t accidentally save half-configured rows
    CHECK (
        storage_type != 'SFTP'
            OR (sftp_host IS NOT NULL AND sftp_remote_path IS NOT NULL AND sftp_base_url IS NOT NULL)
        ),
    CHECK (
        storage_type != 'S3'
            OR (s3_bucket IS NOT NULL)
        )
);

CREATE INDEX idx_rs_storage_type
    ON radio_system_storage_settings (storage_type);


COMMIT;
