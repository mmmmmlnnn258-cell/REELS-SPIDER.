-- 0039 — persist the Zernio profile and map hosted Instagram accounts to
-- existing SocialScheduler channels. No Meta tokens are stored or synthesized.
CREATE TABLE IF NOT EXISTS zernio_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    profile_id TEXT NOT NULL,
    profile_name TEXT NOT NULL DEFAULT 'BLACK-SPIDER',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

ALTER TABLE channels ADD COLUMN zernio_account_id TEXT;
ALTER TABLE channels ADD COLUMN zernio_profile_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_zernio_account_id
    ON channels(zernio_account_id)
    WHERE zernio_account_id IS NOT NULL AND TRIM(zernio_account_id) <> '';
