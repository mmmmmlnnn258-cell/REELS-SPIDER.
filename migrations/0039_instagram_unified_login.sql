-- 0039 — distinguish Unified Login system-user tokens from older Facebook Login tokens.
--
-- Unified Login still uses graph.facebook.com, but its token lifecycle and connection
-- provenance differ. Existing rows remain 0; only the verified OAuth callback sets 1.
-- Additive only: no existing credentials, IDs, queue rows, or channel settings change.

ALTER TABLE channels
  ADD COLUMN instagram_unified_login INTEGER NOT NULL DEFAULT 0
  CHECK (instagram_unified_login IN (0, 1));
