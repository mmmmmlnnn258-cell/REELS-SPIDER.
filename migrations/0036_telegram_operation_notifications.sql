-- 0036 — track Telegram delivery per recipient so a partial multi-chat send
-- can retry only chats that did not receive the operation alert, without starving
-- newer alerts when an older recipient remains unavailable.
CREATE TABLE telegram_operation_notifications (
  id INTEGER PRIMARY KEY,
  operation_id INTEGER NOT NULL REFERENCES telegram_operation_log(id) ON DELETE CASCADE,
  chat_id TEXT NOT NULL,
  last_attempt_at TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  notified_at TEXT,
  UNIQUE(operation_id, chat_id)
);
CREATE INDEX idx_telegram_operation_notifications_operation
  ON telegram_operation_notifications(operation_id, notified_at, last_attempt_at);
