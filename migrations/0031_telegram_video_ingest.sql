-- 0031 — Telegram video-ingest idempotency.
-- One row per Bot API update prevents a retried long-poll response from creating
-- duplicate assets, posts, or publications.
CREATE TABLE telegram_ingest_updates (
  update_id INTEGER PRIMARY KEY,
  chat_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('processed', 'ignored', 'failed')),
  error TEXT,
  post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_telegram_ingest_status ON telegram_ingest_updates(status, created_at);
