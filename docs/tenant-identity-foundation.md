# Tenant identity foundation (Stage A)

Status: additive schema foundation only. This is not a customer-facing multi-tenant release.

## What migration 0039 adds

- `tenants`: independently named customer workspaces.
- `users`: case-insensitive unique login email; the database accepts only the versioned `scrypt$v1$` password-hash envelope, not plaintext passwords.
- `tenant_memberships`: explicit owner/admin/member roles and revocable membership state.
- `tenant_sessions`: expiring/revocable sessions tied by a composite foreign key to a real tenant membership. Only a lowercase SHA-256 token digest is stored.
- `tenant_telegram_chats`: explicit chat-to-tenant mapping, linked by a member of that tenant; a Telegram chat can belong to only one tenant.

## Deliberately not activated

The current installation-wide dashboard login and Telegram allowlist remain unchanged. No default tenant or owner is seeded. No existing channel, post, asset, schedule, token, publication, analytics, or Telegram row is modified. Existing handlers and worker jobs do not yet consume tenant context, so enabling tenant login or customer Telegram control at this stage would be unsafe.

## Next required stages

1. Add and test versioned Node.js `scrypt` password hashing, login, tenant membership resolution, session creation/rotation/revocation, and CSRF/origin protections. Do not accept a tenant ID as proof of membership.
2. Seed a default tenant and an explicitly provisioned owner through a separately reviewed, idempotent migration/bootstrap path.
3. Backfill every tenant-owned business row and add same-tenant relational constraints.
4. Require explicit tenant context in every dashboard API/query, media endpoint, OAuth callback, worker queue, and Telegram action before exposing customer accounts.
5. Add two-tenant negative tests across all read/write and background workflows.

Migration 0039 is safe to review independently because it only creates new tables and indexes. Production migration/deployment still requires explicit approval and a verified backup/restore rehearsal.
