-- 0039 — additive identity and tenant ownership foundation.
--
-- This migration intentionally creates schema only. It does NOT create a default
-- tenant, users, sessions, or Telegram links; does not backfill business tables;
-- and does not change the existing DASHBOARD_USERNAME login. Those cutovers require
-- separate, rehearsed migrations after every route and worker path is tenant-scoped.

CREATE TABLE tenants (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
  slug        TEXT NOT NULL COLLATE NOCASE UNIQUE
              CHECK (length(trim(slug)) BETWEEN 2 AND 80),
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active', 'suspended', 'closed')),
  created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  email          TEXT NOT NULL COLLATE NOCASE UNIQUE
                 CHECK (length(trim(email)) > 2),
  password_hash  TEXT NOT NULL CHECK (password_hash LIKE 'scrypt$v1$%'),
  status         TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'disabled')),
  created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenant_memberships (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active', 'revoked')),
  created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, user_id)
);

-- A session stores only a SHA-256 token digest, never the bearer token.
-- Composite FK ensures a session can only exist for an actual tenant membership.
CREATE TABLE tenant_sessions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL,
  user_id         INTEGER NOT NULL,
  token_hash      TEXT NOT NULL UNIQUE
                  CHECK (length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'),
  created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at      TEXT NOT NULL,
  last_seen_at    TEXT,
  revoked_at      TEXT,
  ip_hash         TEXT,
  user_agent_hash TEXT,
  FOREIGN KEY (tenant_id, user_id)
    REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE CASCADE
);

CREATE INDEX idx_tenant_sessions_membership
  ON tenant_sessions(tenant_id, user_id, expires_at);

CREATE TABLE tenant_telegram_chats (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id          INTEGER NOT NULL,
  chat_id            TEXT NOT NULL UNIQUE CHECK (length(trim(chat_id)) > 0),
  linked_by_user_id  INTEGER NOT NULL,
  status             TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'revoked')),
  created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at         TEXT,
  FOREIGN KEY (tenant_id, linked_by_user_id)
    REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE CASCADE
);

CREATE INDEX idx_tenant_telegram_chats_tenant
  ON tenant_telegram_chats(tenant_id, status);
