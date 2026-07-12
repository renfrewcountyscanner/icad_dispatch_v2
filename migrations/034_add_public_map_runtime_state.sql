BEGIN;

CREATE TABLE IF NOT EXISTS public_map_rate_limits (
    bucket_start TIMESTAMPTZ NOT NULL,
    client_ip TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket_start, client_ip)
);

CREATE TABLE IF NOT EXISTS public_map_broadcasts (
    call_id INTEGER PRIMARY KEY REFERENCES call_records(call_id) ON DELETE CASCADE,
    broadcast_epoch BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_public_map_broadcasts_epoch
    ON public_map_broadcasts (broadcast_epoch);

INSERT INTO schema_migrations (version) VALUES (34)
    ON CONFLICT DO NOTHING;

COMMIT;
