-- 0040 — remember the remote Zernio automation and the last applied rule config.
-- This lets the worker reconcile local edits without creating duplicate automations.
ALTER TABLE instagram_comment_rules ADD COLUMN zernio_automation_id TEXT;
ALTER TABLE instagram_comment_rules ADD COLUMN zernio_config_hash TEXT;
ALTER TABLE instagram_comment_rules ADD COLUMN zernio_media_id TEXT;
