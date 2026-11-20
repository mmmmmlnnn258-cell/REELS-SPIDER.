# Instagram Reel comment automation

This feature is opt-in per Instagram account and Reel.

## Dashboard

Open **Comment replies** in the sidebar. Select an Instagram channel and a Reel from the synced media list (or enter its media ID manually). Add trigger phrases, the URL, and the private/public reply templates. Rules are saved disabled; enable a rule only after checking its content and account credentials.

Template variables:
- `{username}` — commenter username when Meta returns it.
- `{link}` — the rule's configured URL.
- `{media_id}` — the Reel media ID.

## Worker behavior

The worker polls enabled rules every 120 seconds, reads up to three comment pages per rule per cycle, ignores replies to other comments and the account's own comments, and stores each comment ID before sending. This prevents duplicate sends after overlapping polls or restarts. An ambiguous send failure is marked `unknown` and is not retried automatically, because retrying could send a duplicate private reply.

Instagram's official Webhooks documentation says Reels are not supported for the `comments` webhook field, so this feature polls the media comments edge instead. See [Meta's Instagram Webhooks documentation](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-instagram/).

## Meta connection requirements

The worker chooses the Graph API host from each channel's saved login method:
- **Instagram Login:** `graph.instagram.com`.
- **Facebook Login:** `graph.facebook.com`.
- **Legacy channels:** retain the configured `META_GRAPH_BASE` until explicitly reconnected or classified.

Do not change `META_GRAPH_BASE` to switch newly connected accounts. That setting is only the fallback for legacy Instagram channels; each explicitly connected channel keeps its own host, so one private installation can use multiple accounts and both login methods without a global host switch.

Instagram Login uses `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, and `instagram_business_manage_messages` as needed by the enabled features. The app and account must have the permissions/access level Meta requires for the requested operation. See [Meta's Instagram API changelog](https://developers.facebook.com/docs/instagram/platform/instagram-api/changelog/) and [Instagram Messaging changelog](https://developers.facebook.com/documentation/business-messaging/instagram-messaging/changelog/).

Meta limits private replies to one private reply per comment and enforces a seven-day window for comments on posts and Reels. Follow-up messaging is subject to the normal Instagram messaging policy. The worker also avoids retrying uncertain send outcomes.

## Safety

- The rule is disabled when first created.
- The worker never sends private or public replies while `DRY_RUN=1`.
- `KILL_SWITCH=1` stops the worker cycle, including comment automation; `DRY_RUN=1` also prevents comment replies and publishing.
- Do not enable a rule until the channel is connected through OAuth (or its legacy credentials are verified), and Meta has granted the required permissions.
