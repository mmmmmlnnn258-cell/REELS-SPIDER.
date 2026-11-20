# Multi-tenant readiness audit

Status: architecture audit only. This document makes no runtime, database, Railway, or production-data changes.

Audited against the PR #5 head (`4391be84d188b921a4507bd87e2cf02cc1179d70`), including migrations `0001`–`0038`, the dashboard query layer, dashboard authentication, and the Telegram control worker.

## Finding

The application is currently a **single-installation / single-owner system**, not a shared multi-tenant SaaS. It can manage multiple social channels in one installation, but those channels, posts, assets, schedules, analytics, automation rules, and Telegram control state share one SQLite database and one dashboard login.

Adding a `tenant_id` column only to `channels` would not isolate customers. Several core objects are not channel-owned, and many child records are connected through foreign keys or join tables. Both dashboard and worker queries would need tenant scoping.

## Verified current architecture

- Dashboard authentication checks one `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` from environment configuration. The signed 12-hour cookie proves installation access; it does not identify a user or tenant.
- The dashboard proxy requires that installation-wide session for protected pages and APIs. There is no tenant membership or per-customer authorization context.
- Dashboard query helpers use a shared SQLite connection and currently accept record IDs, not a verified tenant context.
- Telegram authorization is an installation-wide `TELEGRAM_ALLOWED_CHAT_IDS` allowlist. It is not a mapping from a Telegram chat to a customer account.
- Telegram state and control include global or chat-keyed records: the publishing pause singleton, schedule sessions, comment-edit sessions, DM reply sessions/outbox, operation log/notifications, and conversation references.
- The worker reads the same database and processes shared publication, metrics, automation, and ingest queues.
- OAuth selects a channel in the shared database. Its state protects the browser callback, but there is no tenant membership to authorize who owns that channel.

## Data ownership inventory

Every customer-owned row must be scoped to a tenant, directly or through a tenant-owned parent. The following inventory is derived from migrations `0001`–`0038`.

| Domain | Tables / records | Required isolation |
|---|---|---|
| Social accounts | `channels`, `channel_groups`, `autofill_lanes` | Tenant ownership; group names unique per tenant; lanes cannot reference another tenant's channel/group |
| Content and media | `posts`, `assets`, `post_assets`, `post_targets`, `caption_variants`, `tags`, `post_tags`, `post_periods`, `periods` | Tenant ownership; all join relationships must remain inside the same tenant; asset deduplication scoped per tenant |
| Publishing | `publications`, `post_metrics`, `publish_limits` | Publication must reference a post and channel from the same tenant; decide which platform quota limits are truly global versus tenant-level |
| Analytics | `remote_media`, `media_metrics`, `account_metrics`, `audience_demographics` | Tenant ownership inherited from channel/media; no cross-tenant joins or aggregate queries |
| Automation | `instagram_comment_rules`, `instagram_comment_events` | Tenant ownership and same-tenant channel/rule references |
| Telegram ingest/control | `telegram_ingest_updates`, `telegram_schedule_sessions`, `telegram_comment_edit_sessions`, `telegram_dm_reply_sessions`, `telegram_dm_outbox`, `telegram_dm_conversation_refs` | Resolve chat to tenant before processing; bind every session, callback reference, and outbox item to that tenant |
| Telegram operations | `telegram_operation_log`, `telegram_operation_notifications` | Tenant-scoped history and recipient delivery; notifications only to chats belonging to that tenant |
| Installation infrastructure | `worker_heartbeat`, process configuration, emergency safety switches | Keep explicitly installation-wide unless a separate per-tenant operational control is designed; distinguish emergency global stop from customer pause |

Additional global uniqueness constraints need redesign before backfill: `assets.content_hash` is globally unique, and `channel_groups.name` is globally unique. Customer data should not collide merely because two tenants upload the same file or name a group the same thing.

## Target architecture

1. **Identity:** add tenants, users, tenant memberships/roles, and revocable sessions. Authenticate a user and derive the active tenant from a verified membership. Never trust a tenant ID supplied by a browser request as authorization.
2. **Tenant context:** require an authenticated tenant context in every dashboard API handler and every data-access function. Pass it explicitly to query helpers; do not use mutable process-global tenant state.
3. **Relational integrity:** add tenant ownership to all tenant-owned root tables and use composite unique keys / foreign keys where needed (for example, publication-to-post-and-channel and post-asset-to-post-and-asset) so a child row cannot connect records from different tenants. Scope natural uniqueness such as asset hashes and group names by tenant.
4. **Telegram routing:** create an explicit tenant-to-Telegram-chat membership/authorization table. Resolve the incoming private chat to one tenant before reading or mutating any business data. Bind callback sessions, DM previews, and outbox rows to both tenant and chat. Keep an operator-level emergency allowlist separate from customer authorization.
5. **Worker isolation:** every queue, automation poll, metrics sync, token refresh, media sync, and publication claim must carry tenant identity or select rows through tenant-owned parents. A job for tenant A must never load tenant B's assets, credentials, publications, or recipients.
6. **OAuth isolation:** bind the OAuth state and selected channel to the authenticated user and tenant; verify channel ownership again in the callback transaction. Duplicate-account policy must be explicit and enforced without leaking whether another tenant has linked an account.
7. **File isolation:** tenant-scope asset metadata and storage paths. Downloads, thumbnails, cover frames, exports, and deletes must authorize the parent asset's tenant, not just an integer ID or opaque path.

## Safe migration and rollout sequence

- **Stage A — identity foundation:** create tenant, user, membership, and session schema plus authentication/authorization helpers. Keep the existing installation operating as one owner during transition.
- **Stage B — additive data migration:** create a default tenant for the existing installation and backfill every existing customer-owned row to it. Preserve IDs, tokens, publication history, schedules, and assets. Do not run this migration against production until a tested backup/restore and rollback plan exists.
- **Stage C — enforce relational boundaries:** rebuild SQLite tables where required to add non-null tenant ownership and composite same-tenant foreign keys. Update unique indexes and verify foreign-key integrity after migration.
- **Stage D — dashboard and files:** convert every API route and query helper to require tenant context; add negative authorization tests for read, create, update, delete, nested resources, and media access.
- **Stage E — worker and Telegram:** propagate tenant context through all worker jobs and Telegram flows; test two tenants with overlapping numeric IDs, identical asset hashes, same group names, and separate chats.
- **Stage F — staged release:** run full CI and migration rehearsal on a copy of production data; deploy only after explicit approval, with a backup and a verified rollback. Keep live publishing disabled until separate end-to-end authorization and platform tests pass.

## Required isolation tests

- A user in tenant A cannot list, read, modify, or delete tenant B's channels, posts, assets, publications, analytics, rules, or operation records, even when B's numeric record ID is known.
- A tenant A publication cannot reference a tenant B post or channel; a tenant A post cannot attach a tenant B asset.
- A tenant A Telegram chat cannot invoke a callback, session, schedule action, DM reply, or notification belonging to tenant B.
- OAuth callbacks cannot connect or replace a channel outside the initiating user's tenant.
- Worker queries and claims for tenant A never select tenant B's due work or credentials.
- File endpoints cannot reveal tenant B's media by guessing IDs or paths.
- Existing installation data is fully backfilled into the default tenant, with counts and foreign-key checks reconciled.

## Explicit non-goals for this audit

No tenant tables or migration have been added yet. No dashboard login behavior, Telegram authorization, worker query, OAuth flow, production database, Railway setting, or live account has been changed. This audit is the implementation checklist; the actual feature must be delivered in small, testable stages rather than presenting a partially scoped `tenant_id` migration as customer isolation.
