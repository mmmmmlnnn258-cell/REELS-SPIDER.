-- 0033 — global pause/resume state for publication processing.
-- A single row controls the publishing queue across every connected channel.
CREATE TABLE publishing_control (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  is_paused INTEGER NOT NULL DEFAULT 0 CHECK (is_paused IN (0, 1)),
  updated_at TEXT NOT NULL,
  updated_by_chat_id TEXT
);

INSERT INTO publishing_control (id, is_paused, updated_at, updated_by_chat_id)
VALUES (1, 0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), NULL);
