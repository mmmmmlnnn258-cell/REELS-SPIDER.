-- 0040 — short-lived, single-use Telegram-originated account-link tickets.
-- Store only a SHA-256 digest of the bearer token. A ticket is bound to the
-- allow-listed Telegram chat/user, one existing Instagram channel.
CREATE TABLE telegram_account_link_tickets (
  id INTEGER PRIMARY KEY,
  ticket_hash TEXT NOT NULL UNIQUE,
  telegram_chat_id TEXT NOT NULL,
  telegram_user_id TEXT NOT NULL,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  platform TEXT NOT NULL CHECK (platform = 'instagram'),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'connected', 'failed', 'expired', 'canceled')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  result_account_name TEXT,
  result_account_id TEXT,
  failure_reason TEXT
);
CREATE INDEX idx_telegram_account_link_tickets_chat
  ON telegram_account_link_tickets(telegram_chat_id, created_at DESC);
CREATE INDEX idx_telegram_account_link_tickets_expiry
  ON telegram_account_link_tickets(status, expires_at);
