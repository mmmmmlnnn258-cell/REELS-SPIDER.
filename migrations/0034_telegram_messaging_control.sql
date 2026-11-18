-- 0034 — BLACK SPIDER comment management and manually confirmed DM replies.
CREATE TABLE telegram_comment_edit_sessions (
  chat_id TEXT PRIMARY KEY,
  rule_id INTEGER NOT NULL REFERENCES instagram_comment_rules(id) ON DELETE CASCADE,
  field TEXT NOT NULL CHECK (field IN ('public_reply_text', 'private_reply_text')),
  created_at TEXT NOT NULL
);

CREATE TABLE telegram_dm_reply_sessions (
  chat_id TEXT PRIMARY KEY,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL,
  recipient_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE telegram_dm_outbox (
  id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL,
  recipient_id TEXT NOT NULL,
  message_text TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'unknown')),
  meta_message_id TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_telegram_dm_outbox_chat ON telegram_dm_outbox(chat_id, created_at DESC);
