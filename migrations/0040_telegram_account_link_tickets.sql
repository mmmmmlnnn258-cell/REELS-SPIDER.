-- 0040 — Instagram accounts are independent of publishing channels.
-- Tickets are single-use, Telegram-bound and contain only a SHA-256 digest.
CREATE TABLE instagram_accounts (
  id INTEGER PRIMARY KEY,
  telegram_chat_id TEXT NOT NULL,
  telegram_user_id TEXT NOT NULL,
  instagram_user_id TEXT NOT NULL,
  username TEXT NOT NULL,
  access_token TEXT NOT NULL,
  token_expires_at TEXT,
  auth_method TEXT NOT NULL DEFAULT 'instagram_login'
    CHECK (auth_method = 'instagram_login'),
  status TEXT NOT NULL DEFAULT 'connected'
    CHECK (status IN ('connected', 'expired', 'revoked', 'error')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_verified_at TEXT NOT NULL,
  UNIQUE (telegram_user_id, instagram_user_id)
);
CREATE INDEX idx_instagram_accounts_owner
  ON instagram_accounts(telegram_chat_id, telegram_user_id, created_at DESC);

CREATE TABLE telegram_account_link_tickets (
  id INTEGER PRIMARY KEY,
  ticket_hash TEXT NOT NULL UNIQUE,
  telegram_chat_id TEXT NOT NULL,
  telegram_user_id TEXT NOT NULL,
  platform TEXT NOT NULL CHECK (platform = 'instagram'),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'connected', 'failed', 'expired', 'canceled')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  result_account_name TEXT,
  result_account_id TEXT,
  failure_reason TEXT,
  notified_at TEXT
);
CREATE INDEX idx_telegram_account_link_tickets_chat
  ON telegram_account_link_tickets(telegram_chat_id, created_at DESC);
CREATE INDEX idx_telegram_account_link_tickets_expiry
  ON telegram_account_link_tickets(status, expires_at);

-- The bot remembers one selected account per Telegram identity.
CREATE TABLE telegram_account_preferences (
  telegram_chat_id TEXT NOT NULL,
  telegram_user_id TEXT NOT NULL,
  selected_instagram_account_id INTEGER REFERENCES instagram_accounts(id) ON DELETE SET NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (telegram_chat_id, telegram_user_id)
);
