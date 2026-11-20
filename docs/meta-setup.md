# Meta setup & safe Instagram connection

A step-by-step to connect an Instagram professional account through official OAuth and
verify the connection without publishing content or sending messages. Keep production
safety switches unchanged until a separate, explicitly approved live test.

> Looking for **Discord** or **Telegram** instead? Those aren't part of Meta at all —
> see **`docs/other-platforms-setup.md`**, a much shorter setup with no public URL or
> tunnel required.

> Verified against Meta docs on 2026-07-22 (API v25.0). Meta iterates the dashboard
> wording, so exact button labels may differ slightly; the panel locations are stable.
> Items marked ⚠ couldn't be pinned to a single authoritative page.

## Before deploying the protected dashboard

This version adds owner-only dashboard login. **Set these three server variables before
deploying it**, or the dashboard will show the setup warning and block access until they
are configured:

```
DASHBOARD_USERNAME=choose-a-private-owner-login
DASHBOARD_PASSWORD=use-a-unique-strong-password
DASHBOARD_SESSION_SECRET=generate-at-least-32-random-characters
```

Keep the username and password private. Generate a random session secret (for example,
`openssl rand -hex 32`) and store all three values in Railway's Variables/secret settings,
not in GitHub or this document. The session secret must remain stable between restarts and
must be at least 32 characters; changing it signs out existing sessions.

## What you need first
- An Instagram **professional** account (Business or Creator). Personal accounts can't publish via API.
- A Meta developer account (developers.facebook.com).

---

## IMPORTANT — check the login mode before creating a Meta app

The OAuth code currently merged into BLACK SPIDER implements the classic Instagram
Business Login flow (`instagram.com/oauth/authorize`, Instagram App ID/Secret, and
the four `instagram_business_*` scopes). It does **not** implement Meta's newer
Unified Login / Facebook Login for Business `config_id` flow.

Recent reports from October–November 2026 indicate that newly created Meta apps using
the "Manage messaging & content on Instagram" use case may be required to use Unified
Login, while the classic Instagram Login setup is unavailable to those apps. Meta's
public documentation and dashboard behavior must be checked for the specific app before
choosing an implementation path. See the [Meta Instagram API changelog](https://developers.facebook.com/docs/instagram/platform/instagram-api/changelog/)
and these recent [Postiz report](https://github.com/gitroomhq/postiz-app/issues/2350) and
[OpenPost follow-up](https://github.com/getopenpost/openpost/issues/495). The latter two
are third-party reports, not a substitute for confirming the exact options shown in your
own Meta developer dashboard.

**Do not create a new Meta app or enter OAuth credentials into Railway yet.** If the new
app dashboard only offers Unified Login, the current BLACK SPIDER OAuth button will not
complete that flow. A code update is required first. Do not try to work around this by
pasting a token manually or changing `META_GRAPH_BASE`.

The next implementation must be designed around the actual configuration Meta provides:
- classic Instagram Business Login for an existing app that still supports it; or
- Unified Login / Facebook Login for Business using the app's `config_id`, if that is
  the mode Meta requires for a newly created app.

These are different authorization and token flows. We must not treat a `config_id`
as an Instagram App ID or assume the classic Instagram token exchange, identity endpoint,
token expiry, or Graph host applies to Unified Login.

## Step 1 — Confirm the Meta app login mode (do not create credentials yet)

1. Open [Meta for Developers](https://developers.facebook.com/) and inspect the app
   creation/use-case flow and the Instagram setup page.
2. If you already have a Meta app, inspect its Instagram setup mode; do not change its
   permissions or credentials.
3. If the dashboard says "API setup with Instagram login is not available for newer apps"
   or shows **API setup with Unified Login**, stop there. Do not enter any credentials in
   Railway. The current code needs a separate Unified Login implementation and test.
4. If Meta explicitly offers the classic **API setup with Instagram login** for an
   existing app, the current OAuth implementation may be compatible with that classic
   path, subject to a real OAuth test after the server is safely configured.

## Step 2 — Secrets and callback (only after the supported flow is confirmed)

Do not configure these values until the implementation and the Meta app's login mode match.
The current classic flow expects:
```
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_REDIRECT_URI=https://your-domain.example/api/channels/instagram/callback
TOKEN_ENCRYPTION_KEY=...
```
The exact callback must be registered in the matching Meta login configuration. Do not
substitute credentials from a different login mode.

Generate `TOKEN_ENCRYPTION_KEY` as canonical Base64 for exactly 32 random bytes:
```sh
python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```
Keep it secret, back it up securely, and use the identical value for dashboard and worker.
Losing or changing it without re-encrypting stored tokens makes connected Instagram
credentials unreadable.

The connected Instagram account must be **Business or Creator**; ordinary personal
accounts are not supported.

## Step 3 — Connect the existing channel with OAuth
1. In SocialScheduler → **Channels**, create an Instagram channel if you do not already
   have one. Creating the channel does not require an account ID or access token.
2. On the exact Instagram channel card you want to connect, press **ربط Instagram**.
3. Sign in directly with Instagram and approve the requested permissions:
   `instagram_business_basic`, `instagram_business_content_publish`,
   and `instagram_business_manage_comments`, `instagram_business_manage_messages`.
4. Meta returns to the registered callback. The server exchanges the code, obtains a
   long-lived token, verifies the account identity, encrypts the token, and updates only
   the selected channel. It refuses to link the same Instagram account to a second channel.
5. Confirm the success message shows the expected Instagram username and channel number.
   The access token remains server-side; do not paste it into chat, GitHub, or the dashboard.
6. The worker automatically selects `graph.instagram.com` for this Instagram Login
   channel. Leave `META_GRAPH_BASE` unchanged; it remains the fallback for legacy channels.

If OAuth fails, read the reason shown on the Channels page. Common causes are a callback URL
that does not exactly match Meta's registered URL, missing Instagram app credentials, a
missing encryption key, an account that is not Business/Creator, or permissions that were
not granted. A failed connection does not overwrite the channel's existing credentials.

## Step 4 — Verify the connection without side effects
Keep `DRY_RUN=1` and `KILL_SWITCH=0`. Check the channel card and system status to confirm
the expected Instagram username is connected to the selected channel. Confirm that the
channel's account ID is populated and that no duplicate channel was created.

If a read-only worker preflight is available in your deployment, it may be used to verify
authentication and account access. Do not use a publish command, send a test DM, or trigger
comment automation as part of this connection check.

## Step 5 — Live actions require separate explicit approval
This guide intentionally stops after connection verification. It does not authorize a real
post, comment reply, private message, or any change to production safety switches. Keep
`DRY_RUN=1` and `KILL_SWITCH=0`. Before any live test, review the destination account,
content, permissions, queue, and rollback plan, and obtain explicit approval from the
account owner. Do not schedule a post in the past as a substitute for a controlled test.

---

## Before onboarding paying customers

The Development-mode setup above is for the app owner and people who have an eligible role on the Meta app. It is **not** a public customer rollout.

Before allowing unrelated customers to connect their own Instagram accounts:

1. Complete Meta App Review and obtain **Advanced Access** for every permission the product actually uses, including the four documented Instagram Login scopes: `instagram_business_basic`,
   `instagram_business_content_publish`, `instagram_business_manage_comments`, and
   `instagram_business_manage_messages`. Standard Access is limited to people with a role on the app; Advanced Access is required for ordinary users.
2. If the app was created or claimed by a business, check Meta's **Tech Provider verification** requirement. Meta states that business-created/claimed apps cannot receive certain business permissions from users outside the app's roles unless the owning business is verified as a Tech Provider. This verification is separate from App Review; both may be required.
3. Complete any business verification and data-handling requirements Meta presents, then test OAuth with a non-role customer account before announcing availability.
4. Keep each customer's Instagram account connected only to their own authorized workspace/channel. Do not share access tokens between customers.

These are Meta platform approval requirements, not steps that a code change or a successful CI run can bypass. Approval is controlled by Meta and is not guaranteed.

Official Meta references:
- [App Review and Advanced Access](https://developers.facebook.com/docs/resp-plat-initiatives/app-review/introduction)
- [Tech Provider verification](https://developers.facebook.com/docs/development/release/tech-providers/)
- [Data handling and access levels FAQ](https://developers.facebook.com/docs/resp-plat-initiatives/individual-processes/data-handling-questions/faqs/)

---

## Notes / gotchas
- Media containers **expire after 24h** — the worker publishes promptly, so this only matters
  if a post sits failed for a day.
- Publishing rate limit is read live per account (Meta's own docs disagree on 50 vs 100) — the
  worker paces itself; you don't set a number. (Instagram only — Pages have no such endpoint
  and aren't gated.)
- Our uploader currently accepts JPEG/PNG/WebP, but **Meta only accepts JPEG for image posts** —
  use JPEG for anything you'll actually publish. (A validation tightening we can add.)

---

## When a token misbehaves

Start here, always — before changing any setting:

```
.venv/bin/python -m worker.exchange_token --check
```

It reports what every stored token actually *is* — kind, permissions, expiry — and changes
nothing. Meta's publish errors name none of that, so guessing from the error alone sends you
in circles.

```
[1] Liparoto (instagram): reachable as @liparoto (expiry not reported)
[4] Liparoto (facebook):  PAGE token, never, pages_manage_posts=yes
```

**`(#200) Permissions error` on a Facebook publish** has exactly two causes, and `--check`
tells you which:

- **`pages_manage_posts=NO`** — the token lacks the publishing permission. Adding it to the
  app afterwards does nothing to a token already issued; you must add it to the *"Manage
  everything on your Page"* use case, then generate a **new** token (steps 2–4 above).
- **An expiry date instead of `never`** — the token was copied from `me/accounts` without
  the extension step. Re-run `worker.exchange_token`.

Two things that will *not* tell you a token is broken, so don't rely on them: `preflight`
passes on a read-only token (reads work, publishing doesn't), and the dashboard shows the
channel as connected either way.

Instagram Login is the exception — Meta's `debug_token` refuses Instagram-Login tokens
(`(#2) Service temporarily unavailable`), so `--check` is read-only: it proves the token can
reach `/me` and reports the username, but it cannot report the expiry or renew the token.

For channels connected with the dashboard's **Connect Instagram** OAuth button
(`instagram_auth_method=instagram_login`), the worker handles renewal automatically. It
checks each active channel every 12 hours and attempts a refresh when the stored expiry is
unknown or within 14 days. A token less than 24 hours old cannot yet be refreshed; the
worker verifies that it still works and tries again on a later cycle. A successful refresh
stores the replacement token and its new expiry. If Meta says the token is expired/revoked
or renewal cannot be recovered, reconnect that channel from **Channels → Connect Instagram**.

The interactive `worker.exchange_token` helper is a separate manual/legacy token workflow;
its reminder to refresh a pasted token before 60 days does not describe OAuth-managed
channels. Do not use `--check` as a renewal command — it never changes stored credentials.

---

## Adding a Facebook Page

Publishing to your own Page works with your app in **Development mode** — no App Review —
as long as you're an **admin** on both the app and the Page. Same arrangement as Instagram.

1. **Nothing to change in `.env`.** Facebook Pages are always reached on
   `graph.facebook.com`, and the worker picks the right host per channel automatically —
   leave `META_GRAPH_BASE` exactly as it is. (This matters if your Instagram channel is on
   the Instagram-Login path — `META_GRAPH_BASE=https://graph.instagram.com` — because
   changing that setting for Facebook's sake would break every Instagram publish.)
2. **Tell your app it manages Pages — do this BEFORE the Explorer.** Meta scopes an app's
   available permissions to its *use cases*, so `pages_manage_posts` (the one that actually
   publishes) is hidden until you add the Page use case. In the App Dashboard:
   **developers.facebook.com/apps** → your app → **Dashboard** → add/customize the
   **"Manage everything on your Page"** use case → click **Add** next to `pages_manage_posts`.
   Skip this and step 3's permission list will offer only the read-only Page permissions.
   (Adding a use case doesn't remove existing ones — an app set up for Instagram keeps working,
   and any already-issued Instagram token is unaffected.)
   Ignore "Advanced Access / requires review" labels: for **your own** Page, with the app in
   **Development mode** and you an admin of both app and Page, Standard Access is enough.
3. **Give your app the permissions.** In the Graph API Explorer, pick your app and request
   `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`. When the approval popup
   asks which Pages to allow, **select every Page you want to manage** — Pages you leave
   unticked simply won't appear later.
4. **Get the permanent Page token — in the dashboard.** **Channels → Add channel** →
   Platform **Facebook Page**. Paste the **user token** from the Explorer's *Access Token*
   box into the **Connect** panel and press **Connect**.

   That is the whole step. The dashboard extends the short-lived token to a long-lived
   one, lists the Pages you administer, and — once you pick one — derives that Page's
   **permanent** token, verifies it, and fills in the Page id, the account name and the
   token for you. Press **Save channel**. Nothing is stored until you do.

   The user token is used once and discarded; it is never saved and never leaves your
   machine except to Meta. Pages you lack publishing rights on appear greyed out with the
   role you are missing, rather than silently vanishing from the list.

   **Prefer the terminal, or on a machine with no dashboard running?** The original helper
   still does the same job, and is still the only path for Instagram and Threads:

   ```
   .venv/bin/python -m worker.exchange_token
   ```

   **Do not shortcut this by copying a token out of `me/accounts` directly.** That token
   inherits the ~1 hour lifetime of the short-lived user token behind it, and looks
   identical to a permanent one. This is the single most common way this setup goes wrong:
   everything works, and then it doesn't. The helper refuses to save such a token.

   Both paths verify the same things before saving — each one a real failure that
   otherwise surfaces hours later as an unexplained `(#200)`:

   | Check | Why |
   |---|---|
   | `type` is `PAGE` | a user token reads your Page fine and never publishes |
   | `pages_manage_posts` present | its absence *is* the `(#200) Permissions error` |
   | `expires_at` is `0` | catches the short-lived-token trap above |
   | Page matches the channel | stops Page B's token overwriting Page A's channel |

   If any check fails it stops and changes nothing, so a bad token can't replace a working one.

5. **Add the channel** (if you haven't). Dashboard → **Channels → Add channel**, platform
   **Facebook Page**, Page id in the id field. Re-run the command above to store the token.
   It's kept only in this install's local database.

<details><summary>Doing it by hand instead</summary>

Run `me/accounts` in the Explorer and note your Page's `id` — check `tasks` includes
`CREATE_CONTENT` and `MANAGE`, which is what proves you can publish. An empty
`{"data": []}` means you administer no Pages; create one first (see the note below).

Then, because that Page token expires in ~1 hour: copy it → open
**developers.facebook.com/tools/debug/accesstoken/** → paste → **Debug** →
**Extend Access Token** (this returns a long-lived *user* token) → put that extended token
back in the Explorer and run `me/accounts` **again**. The Page `access_token` in *this*
result is permanent. Paste it into the dashboard.

The round trip exists only to keep your app secret out of the browser URL bar, which the
`fb_exchange_token` method needs. The helper does the same exchange locally instead.
</details>
6. **Verify without posting.** Run `python3 -m worker.preflight` — it checks credentials and
   publishes nothing. For a Facebook channel this is a plain Page read (Pages have no
   publish-quota endpoint like Instagram's), so a `✓` here means the token and Page id work.
   Then schedule a post with `DRY_RUN=1` and confirm the worker logs the plan. Only then set
   `DRY_RUN=0` for a real post.

**A Page is the only option — and it doesn't have to be a business.** Facebook has allowed no
API publishing to a personal **profile** since 2018 (`publish_actions`), and there is no
replacement and no permission that unlocks it. Instagram's own "also share to Facebook" toggle
*can* post to your profile, but that's a first-party Accounts Center feature and grants outside
tools nothing — don't take it as a sign the API can do the same. If you don't have a Page, make
one at **facebook.com/pages/create**; a category like *Digital Creator* is fine, it takes about
two minutes, and it doesn't alter your personal profile in any way.

**What gets published.** A single-image post goes up in one call. A multi-image post uploads
each photo unpublished, then attaches them to one feed post (Facebook's equivalent of a
carousel). Videos, Reels and Stories aren't supported yet. Images are conformed to
Instagram's shape today (cropped/padded to the 4:5–1.91:1 range) even though Facebook itself
accepts most aspect ratios — Facebook just gets the same derivative Instagram would use.

**About the numbers.** Reactions, comments and shares are always available, and those are what
a Facebook row shows in the queue. Reach/views is different: it depends on a Facebook insights
metric name, and Meta retired a batch of those in June 2026 and keeps changing them. When the
metric works, reach appears alongside the other three; when it doesn't, reach is simply left
out and everything else still works. You can point it at a different metric with
`FB_POST_INSIGHT_METRICS` in `.env`.

**Gotcha: a failed multi-image post can leave orphaned photos.** If photo 3 of 5 fails to
upload, photos 1 and 2 are already sitting in the Page's media library as unpublished
photos — they're harmless (nothing is posted to the feed until all uploads succeed) but
they aren't cleaned up automatically, and a retry uploads fresh copies rather than reusing
them. You can safely delete the leftover unpublished photos from Meta Business Suite's media
library if you want to tidy up.

---

## Adding a Threads account

**Important: Threads Login is its own separate thing.** Even though Threads is a Meta
product, it does **not** reuse your Instagram or Facebook setup. It's a different login
flow, with its own product to add in the App Dashboard and its own permissions
(`threads_basic`, `threads_content_publish`).

**About the Threads user id — it is NOT your Instagram user id.** Meta's docs don't state this
either way, but it was confirmed on a real linked account (2026-07-25): the same person's
Instagram id was `1784140…` while Threads returned a completely different `2786950…`. Using the
Instagram id gets you `THApiException` code 100, *"Object with ID … does not exist"* — which
looks like a broken token but isn't. Get the real one with a single call using your Threads
token:
```
curl -s "https://graph.threads.net/v1.0/me?fields=id,username&access_token=YOUR_THREADS_TOKEN"
```
Whatever `id` comes back is what this section means by "Threads user id". (Verified 2026-07-25:
`GET /me?fields=id,username` on `graph.threads.net` is the documented way to obtain it; Meta
publishes no statement relating it to Instagram identifiers.)

1. **Add the Threads product to your app.** developers.facebook.com/apps → your app →
   **Dashboard** → find **Threads** in the product list → **Set up**. (If your app was
   created for the Instagram or Facebook use case, this is an *additional* product — it
   doesn't remove or change what's already working.)
2. **Authorize your account and get a token.** Open the Threads product's **Login** /
   **Generate access tokens** panel. Click **Add account** (or **Generate token** for your
   own account, depending on how Meta is currently labeling this panel), log in with the
   Threads account you want to connect, and approve the permissions
   (`threads_basic`, `threads_content_publish`). **Copy the token immediately — it isn't
   shown again.** Like the Instagram token, this first one is short-lived.
3. **Exchange it for a long-lived token.** Same idea as Instagram's Step 3, but Threads has
   its own exchange endpoint:
   ```
   curl -s "https://graph.threads.net/access_token\
   ?grant_type=th_exchange_token\
   &client_secret=YOUR_APP_SECRET\
   &access_token=SHORT_LIVED_TOKEN"
   ```
   The returned `access_token` is valid ~60 days. Refresh it before expiry the same way you
   would an Instagram token, using Threads' own refresh endpoint — ask me if you want the
   exact call when you're closer to that date.
4. **Find your Threads user id.** Easiest: in the dashboard, **Channels → Add channel** →
   Threads, paste the long-lived token into **Access token**, then click **"Don't know it?
   Look it up from your access token"** under the id field. It fills in the id and shows
   the username, so you can confirm it is the account you meant.

   The token-generation panel usually shows the id too, and the equivalent by hand is:
   ```
   curl -s "https://graph.threads.net/v1.0/me?fields=id,username&access_token=YOUR_THREADS_TOKEN"
   ```
   Use the `id` it returns. Check the `username` matches the account you meant to connect —
   that one line confirms both the token and the id in a single call.
5. **Add the channel in the dashboard.** **Channels → Add channel**:
   - Platform: **Threads**
   - Account name: whatever label helps you tell it apart (e.g. `Liparoto Threads`)
   - Timezone: e.g. `America/New_York`
   - **Threads user id**: from Step 4
   - **Access token**: the long-lived token from Step 3
6. **Verify without posting.** From the repo root, with the venv active:
   ```
   source .venv/bin/activate
   python -m worker.preflight
   ```
   For a Threads channel this reads your real publishing quota — a
   `✓ ... published N/M in the last 24h window` line means the token and Threads user id
   are valid. It posts nothing. Only move on to a real post once this comes back clean.

**Limits you'll actually hit:**
- **500 characters** per post (caption/body) — the composer's character counter enforces
  this before you can even save, and the worker enforces it again independently.
- **Carousels need 2–20 images** — fewer than 2 or more than 20 is rejected before anything
  is sent to Meta. (Instagram's own carousel cap is lower, 10 — don't assume the same
  number applies here.)
- **250 published posts per rolling 24 hours.** Unlike Facebook Pages, Threads *does* expose
  a real quota endpoint, and the worker reads it live before every publish rather than
  guessing — if you're at 250/250 it simply defers the post and retries later rather than
  failing.

**Text-only posts are a Threads thing.** Threads is the only platform here that can publish
a post with no image at all — the composer's **"Text only"** toggle hides the image picker
and switches the character counter to whatever the strictest limit is among the channels
you've selected. If you toggle it on while an Instagram or Facebook channel is also
selected, those channels get deselected automatically, because they can't publish text —
and if a text post ever did reach the worker aimed at one of them anyway, the worker itself
would refuse it outright (a clear, permanent failure, not a retry) rather than silently
dropping the text or guessing at an image.


## Instagram Login token encryption

Instagram Login access tokens are stored in SQLite as versioned AES-256-GCM envelopes.
The dashboard encrypts the token when OAuth completes; the Python worker decrypts it
inside its SQLite row boundary and encrypts any renewed token before writing it back.
Existing plaintext legacy/Facebook Login/TikTok credentials are left unchanged.

Set `TOKEN_ENCRYPTION_KEY` in the shared runtime environment before completing the
first Instagram Login connection. It must be canonical Base64 encoding of exactly 32
random bytes. Generate one with:

```sh
python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Use the SAME key for the dashboard and worker and every replica sharing the database.
Do not commit the key or put it in client-visible settings. Keep a secure backup: losing
or changing it without re-encrypting the stored tokens makes those Instagram Login
connections unreadable. Missing or incorrect keys and tampered ciphertext fail closed;
legacy plaintext tokens remain readable.
