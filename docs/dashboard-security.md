# Dashboard access protection

The dashboard is protected by a server-side username/password login. The protection applies to dashboard pages and every `/api/*` route except the login/logout endpoints and the provider callback endpoints that must receive OAuth redirects.

## Required server environment

Set these values before deploying the protected dashboard:

- `DASHBOARD_USERNAME`: the owner login name.
- `DASHBOARD_PASSWORD`: a unique, strong password.
- `DASHBOARD_SESSION_SECRET`: a cryptographically random secret with at least 32 characters.

Do not commit real values, place them in source code, or share them in chat. Store them only in the deployment platform's secret/environment-variable manager. If any value is missing or the session secret is shorter than 32 characters, login fails closed and protected pages/APIs remain inaccessible.

The login cookie is HTTP-only, Secure in production, SameSite=Lax, and expires after 12 hours. Session signatures are verified on the server. Unsafe API requests also require a same-origin Origin header. Login attempts are rate-limited per normalized username: five failed attempts trigger a 15-minute lockout, and a successful login clears that username’s failure counter. The limiter is process-local; deployments with multiple dashboard instances should add a shared store or edge-level rate limiting before exposing the login publicly.

## OAuth callback exceptions

Instagram and TikTok callback URLs remain reachable without an existing dashboard session because the provider redirects the browser to them after the user authorizes the app. The callbacks must still pass their existing state/PKCE checks before they can change account credentials. OAuth authorize endpoints and all other dashboard APIs remain protected.

## Rollout

Configure the three variables in the target server environment before deploying this code. Do not deploy the protected build with missing values: it intentionally fails closed. After deployment, verify that an unauthenticated browser is redirected to `/login`, unauthenticated API requests return HTTP 401, a valid login opens the dashboard, logout clears the session, and both OAuth callbacks reject invalid state.
