-- 0032 — persistent Telegram scheduling conversation state.
-- A private chat can enter a reschedule flow and submit a local date/time in a
-- subsequent message. Persist the state so worker restarts do not lose the flow.
CREATE TABLE telegram_schedule_sessions (
  chat_id TEXT PRIMARY KEY,
  publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
  timezone TEXT NOT NULL,
  created_at TEXT NOT NULL
);
