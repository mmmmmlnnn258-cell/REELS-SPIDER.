# Dashboard access protection

The dashboard is protected by a server-side username/password login. The protection applies to dashboard pages and every `/api/*` route except the login/logout endpoints and the provider callback endpoints that must receive OAuth redirects.

## Required server environment

Set these values before deploying the protected dashboard:

- `DASHBOARD_USERNAME`: the owner login name.
- `DASHBOARD_PASSWORD`: a unique, strong password.
- `DASHBOARD_SESSION_SECRET`: a cryptographically random secret with at least 32 characters.

Do not commit real values, place them in source code, or share them in chat. Store them only in the deployment platform's secret/environment-variable manager. If any value is missing or the session secret is shorter than 32 characters, login fails closed and protected pages/APIs remain inaccessible.

The login cookie is HTTP-only, Secure in production, SameSite=Lax, and expires after 12 hours. Session signatures are verified on the server. Unsafe API requests also require a same-origin Origin header.

Login attempts are rate-limited by the connecting client IP: five failed attempts from the same address within 15 minutes trigger a lockout until that window ends. A successful login clears that address's counter. The limiter stores only an HMAC-SHA-256 digest of the client address, keyed with `DASHBOARD_SESSION_SECRET` in the existing SQLite database, so counters survive dashboard process restarts and are shared by processes that use the same database file. The login handler uses the right-most valid address in `X-Forwarded-For` (the address appended by the trusted reverse proxy), falling back to `X-Real-IP`; ensure the dashboard is reachable only through that trusted proxy. If neither header contains a valid address, requests share an `unknown` bucket.

This is suitable for the current single-service/single-volume deployment, not a horizontally scaled deployment with separate SQLite files. Before adding independent dashboard replicas, move the limiter to a shared network store or enforce it at the trusted edge.

## OAuth callback exceptions

Instagram and TikTok callback URLs remain reachable without an existing dashboard session because the provider redirects the browser to them after the user authorizes the app. The callbacks must still pass their existing state/PKCE checks before they can change account credentials. Instagram also validates the browser-bound state before processing an authorization-denied response; callbacks with missing or mismatched state do not clear the cookies for an in-progress sign-in. OAuth authorize endpoints and all other dashboard APIs remain protected.

## Rollout

Configure the three variables in the target server environment before deploying this code. Do not deploy the protected build with missing values: it intentionally fails closed. After deployment, verify that an unauthenticated browser is redirected to `/login`, unauthenticated API requests return HTTP 401, a valid login opens the dashboard, logout clears the session, and both OAuth callbacks reject invalid state.
