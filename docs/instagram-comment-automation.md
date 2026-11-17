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

The API host must match the login/token flow:
- Instagram Login (no Facebook Page): set `META_GRAPH_BASE=https://graph.instagram.com`.
- Facebook Login: use `META_GRAPH_BASE=https://graph.facebook.com`.

The Instagram Login product introduced the `instagram_business_basic`, `instagram_business_manage_comments`, and `instagram_business_manage_messages` permissions. The app must have the required permissions/access level for the account and operation. See [Meta's Instagram API changelog](https://developers.facebook.com/docs/instagram/platform/instagram-api/changelog/) and [Instagram Messaging changelog](https://developers.facebook.com/documentation/business-messaging/instagram-messaging/changelog/).

Meta limits private replies to one private reply per comment and enforces a seven-day window for comments on posts and Reels. Follow-up messaging is subject to the normal Instagram messaging policy. The worker also avoids retrying uncertain send outcomes.

## Safety

- The rule is disabled when first created.
- The worker never sends private or public replies while `DRY_RUN=1`.
- Keep `KILL_SWITCH=1` available as the emergency stop.
- Do not enable a rule until the channel has a valid token, the correct `META_GRAPH_BASE`, and the required Meta permissions.
