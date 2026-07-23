CREATE TABLE IF NOT EXISTS talkgroup_system_routes (
    talkgroup TEXT PRIMARY KEY,
    target_radio_system_id INTEGER NOT NULL
        REFERENCES radio_systems (radio_system_id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

-- The shared receiver sends TG 18 through another system's API key, but it
-- belongs to Lanark operationally.
INSERT INTO talkgroup_system_routes (talkgroup, target_radio_system_id)
SELECT '18', radio_system_id
FROM radio_systems
WHERE system_name = 'Lanark'
ON CONFLICT (talkgroup) DO UPDATE
SET target_radio_system_id = EXCLUDED.target_radio_system_id, enabled = 1;

INSERT INTO schema_migrations (version) VALUES (37)
ON CONFLICT DO NOTHING;
