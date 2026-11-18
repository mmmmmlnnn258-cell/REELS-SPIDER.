-- 0038 — record the Instagram authentication path per channel.
--
-- Existing rows are deliberately marked legacy: their original login path cannot be
-- inferred safely from the current schema. The worker keeps their historical
-- META_GRAPH_BASE behavior until an explicit reconnect records a concrete path.
-- Additive only; credentials, account ids, page ids, queue rows and channel settings
-- are left untouched.

ALTER TABLE channels
  ADD COLUMN instagram_auth_method TEXT NOT NULL DEFAULT 'legacy'
  CHECK (instagram_auth_method IN ('legacy', 'facebook_login', 'instagram_login'));
