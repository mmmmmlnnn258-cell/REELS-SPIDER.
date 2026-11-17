-- 0030 — opt-in Instagram Reel comment automation.
CREATE TABLE instagram_comment_rules (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  media_id TEXT NOT NULL,
  keywords_json TEXT NOT NULL DEFAULT '[]',
  link_url TEXT,
  private_reply_text TEXT,
  public_reply_text TEXT,
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  last_polled_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT,
  UNIQUE(channel_id, media_id)
);
CREATE INDEX idx_ig_comment_rules_due ON instagram_comment_rules(enabled, last_polled_at);

CREATE TABLE instagram_comment_events (
  comment_id TEXT PRIMARY KEY,
  rule_id INTEGER NOT NULL REFERENCES instagram_comment_rules(id) ON DELETE CASCADE,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  media_id TEXT NOT NULL,
  comment_text TEXT,
  status TEXT NOT NULL CHECK (status IN ('processing','ignored','expired','sent','unknown')),
  private_reply_id TEXT,
  public_reply_id TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_ig_comment_events_rule ON instagram_comment_events(rule_id, created_at);
