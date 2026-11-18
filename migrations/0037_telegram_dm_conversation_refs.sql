-- 0037 — short, chat-bound Telegram callback references for opaque Meta conversation IDs.
CREATE TABLE telegram_dm_conversation_refs (
  id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_telegram_dm_conversation_refs_chat
  ON telegram_dm_conversation_refs(chat_id, created_at DESC);
