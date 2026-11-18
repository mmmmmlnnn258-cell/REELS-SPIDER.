-- 0035 — durable BLACK SPIDER operation history and notification idempotency.
CREATE TABLE telegram_operation_log (
  id INTEGER PRIMARY KEY,
  event_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'retrying', 'failed', 'dry_run', 'info')),
  publication_id INTEGER REFERENCES publications(id) ON DELETE SET NULL,
  channel_name TEXT,
  caption TEXT,
  detail TEXT,
  created_at TEXT NOT NULL,
  notified_at TEXT
);
CREATE INDEX idx_telegram_operation_log_created
  ON telegram_operation_log(created_at DESC, id DESC);
