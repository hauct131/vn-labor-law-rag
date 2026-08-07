BEGIN;

ALTER TABLE app_users ADD COLUMN IF NOT EXISTS email VARCHAR(320);
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS display_name VARCHAR(120);
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS is_anonymous BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

UPDATE app_users
SET updated_at = created_at
WHERE updated_at IS NULL;

ALTER TABLE app_users
    ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE app_users
    ALTER COLUMN updated_at SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ix_app_users_email ON app_users(email);

CREATE TABLE IF NOT EXISTS user_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL,
    csrf_token_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_sessions_token_hash ON user_sessions(token_hash);
CREATE INDEX IF NOT EXISTS ix_user_sessions_expires_at ON user_sessions(expires_at);

COMMIT;
