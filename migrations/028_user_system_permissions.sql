-- Migration 028: User System Permissions
-- Adds user admin status, active flag, and per-system permissions

BEGIN;

-- Add is_admin flag to users (0 = regular user, 1 = admin)
ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;

-- Add is_active flag to users (0 = disabled, 1 = active)
ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

-- Create user_system permissions table
CREATE TABLE IF NOT EXISTS user_systems (
    user_system_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    radio_system_id INTEGER NOT NULL,
    permission_level TEXT NOT NULL DEFAULT 'read'
        CHECK (permission_level IN ('read', 'write')),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (radio_system_id) REFERENCES radio_systems(radio_system_id) ON DELETE CASCADE,
    UNIQUE (user_id, radio_system_id)
);

CREATE INDEX IF NOT EXISTS idx_user_systems_user_id ON user_systems(user_id);
CREATE INDEX IF NOT EXISTS idx_user_systems_radio_system_id ON user_systems(radio_system_id);

-- Migrate existing users: give them write access to all systems
-- This only runs if user_systems is empty (first migration run)
INSERT INTO user_systems (user_id, radio_system_id, permission_level)
SELECT u.user_id, rs.radio_system_id, 'write'
FROM users u
CROSS JOIN radio_systems rs
WHERE NOT EXISTS (
    SELECT 1 FROM user_systems us
    WHERE us.user_id = u.user_id AND us.radio_system_id = rs.radio_system_id
);

COMMIT;