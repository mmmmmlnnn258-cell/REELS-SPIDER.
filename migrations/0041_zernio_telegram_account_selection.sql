-- 0041 — bind Zernio-hosted Instagram accounts to their Telegram owner.
-- The Zernio account remains represented by its publishing channel; do not create
-- a fake instagram_accounts row because Zernio does not expose a Meta access token.
ALTER TABLE channels ADD COLUMN zernio_telegram_chat_id TEXT;
ALTER TABLE channels ADD COLUMN zernio_telegram_user_id TEXT;
CREATE INDEX idx_channels_zernio_telegram_owner
  ON channels(zernio_telegram_chat_id, zernio_telegram_user_id)
  WHERE zernio_account_id IS NOT NULL;

-- Keep legacy account selection intact while allowing a Zernio channel to be selected.
ALTER TABLE telegram_account_preferences
  ADD COLUMN selected_zernio_channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL;
