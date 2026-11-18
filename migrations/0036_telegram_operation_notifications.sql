-- 0036 — track Telegram delivery per recipient so a partial multi-chat send
-- can retry only the chats that did not receive the operation alert.
CREATE TABLE telegram_operation_notifications (
  id INTEGER PRIMARY KEY,
  operation_id INTEGER NOT NULL REFERENCES telegram_operation_log(id) ON DELETE CASCADE,
  chat_id TEXT NOT NULL,
  notified_at TEXT NOT NULL,
  UNIQUE(operation_id, chat_id)
);
CREATE INDEX idx_telegram_operation_notifications_operation
  ON telegram_operation_notifications(operation_id, notified_at);
