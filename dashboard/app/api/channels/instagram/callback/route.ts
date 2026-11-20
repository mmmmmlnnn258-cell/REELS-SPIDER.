import { NextRequest, NextResponse } from "next/server";
import { connectInstagramChannel, getChannel } from "@/lib/queries";
import { config, instagramCredentials, instagramRedirectUri } from "@/lib/config";
import { INSTAGRAM_GRAPH_BASE, INSTAGRAM_SCOPES } from "@/lib/instagram-oauth";

export const runtime = "nodejs";

const STATE_COOKIE = "instagram_oauth_state";
const CHANNEL_COOKIE = "instagram_oauth_channel_id";
const MODE_COOKIE = "instagram_oauth_mode";
const REQUEST_TIMEOUT_MS = 20_000;

type MetaIdentity = {
  id?: number | string;
  user_id?: number | string;
  username?: string;
};

type MetaShortToken = {
  access_token?: string;
  user_id?: number | string;
  permissions?: string | string[];
};

type MetaPayload = MetaIdentity & MetaShortToken & {
  token_type?: string;
  expires_in?: number;
  data?: Array<MetaIdentity & MetaShortToken> | MetaPayload;
  scopes?: string[];
  is_valid?: boolean;
  app_id?: string;
  paging?: { next?: string };
  error?: unknown;
};

function back(
  redirectUri: string,
  params: Record<string, string>,
  clearOAuthCookies = true,
) {
  const url = new URL("/channels", new URL(redirectUri).origin);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  const response = NextResponse.redirect(url);
  response.headers.set("Cache-Control", "no-store");
  // Never let an unsolicited/mismatched callback erase a legitimate in-progress
  // OAuth transaction in this browser.
  if (clearOAuthCookies) {
    response.cookies.delete(STATE_COOKIE);
    response.cookies.delete(CHANNEL_COOKIE);
  }
  return response;
}

function grantedPermissions(value: string | string[] | undefined): Set<string> {
  const entries = Array.isArray(value) ? value : typeof value === "string" ? value.split(",") : [];
  return new Set(entries.map((permission) => permission.trim()).filter(Boolean));
}

async function readJson(response: Response): Promise<MetaPayload | null> {
  try {
    const value: unknown = await response.json();
    return value && typeof value === "object" ? (value as MetaPayload) : null;
  } catch {
    return null;
  }
}

/**
 * Finish Instagram Business Login entirely on the server:
 * authorization code -> short-lived token -> 60-day token -> verified IG identity.
 * The token is stored only after identity and target-channel checks pass.
 */

/**
 * Unified Login returns a Facebook Graph access token (often a non-expiring
 * System User Access Token), not an Instagram short-lived token. Discover the
 * Instagram professional asset explicitly; never infer it from /me or a Page.
 */
async function finishUnifiedLogin(
  redirectUri: string,
  channelId: number,
  code: string,
) {
  const { metaAppId, metaAppSecret, graphVersion } = config;
  if (!metaAppId || !metaAppSecret) {
    return back(redirectUri, {
      instagram_error: "Unified Login requires META_APP_ID and META_APP_SECRET on the server. No channel credentials were changed.",
    });
  }

  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  try {
    const tokenUrl = new URL(`/${graphVersion}/oauth/access_token`, "https://graph.facebook.com");
    tokenUrl.searchParams.set("client_id", metaAppId);
    tokenUrl.searchParams.set("client_secret", metaAppSecret);
    tokenUrl.searchParams.set("redirect_uri", redirectUri);
    tokenUrl.searchParams.set("code", code);
    const tokenResponse = await fetch(tokenUrl, { method: "GET", signal: timeout, cache: "no-store" });
    const tokenPayload = await readJson(tokenResponse);
    if (!tokenResponse.ok || !tokenPayload?.access_token) {
      return back(redirectUri, {
        instagram_error: "Meta could not exchange the Unified Login authorization code. Check the app configuration and exact callback URL. No channel credentials were changed.",
      });
    }

    const accessToken = tokenPayload.access_token;
    const appToken = `${metaAppId}|${metaAppSecret}`;
    const debugUrl = new URL(`/${graphVersion}/debug_token`, "https://graph.facebook.com");
    debugUrl.searchParams.set("input_token", accessToken);
    debugUrl.searchParams.set("access_token", appToken);
    const debugResponse = await fetch(debugUrl, { method: "GET", signal: timeout, cache: "no-store" });
    const debugPayload = await readJson(debugResponse);
    const debug = debugPayload?.data as (MetaPayload | undefined);
    if (!debugResponse.ok || !debug?.is_valid || (debug.app_id && debug.app_id !== metaAppId)) {
      return back(redirectUri, {
        instagram_error: "Meta returned a token that could not be verified for this app. No channel credentials were changed.",
      });
    }

    const required = [
      "instagram_basic",
      "instagram_content_publish",
      "instagram_manage_comments",
      "instagram_manage_messages",
      "business_management",
    ];
    const granted = new Set(Array.isArray(debug.scopes) ? debug.scopes : []);
    const missing = required.filter((permission) => !granted.has(permission));
    if (missing.length) {
      return back(redirectUri, {
        instagram_error: "The Unified Login token is missing required permissions: " + missing.join(", ") + ". Add them to the Facebook Login for Business configuration, then reconnect. No channel credentials were changed.",
      });
    }

    const accountsUrl = new URL(`/${graphVersion}/me/assigned_instagram_accounts`, "https://graph.facebook.com");
    accountsUrl.searchParams.set("fields", "id,username");
    accountsUrl.searchParams.set("limit", "100");
    accountsUrl.searchParams.set("access_token", accessToken);
    const accountsResponse = await fetch(accountsUrl, { method: "GET", signal: timeout, cache: "no-store" });
    const accountsPayload = await readJson(accountsResponse);
    const accounts = Array.isArray(accountsPayload?.data) ? accountsPayload.data : [];
    if (!accountsResponse.ok || accountsPayload?.paging?.next) {
      return back(redirectUri, {
        instagram_error: "Meta could not return one complete list of Instagram accounts for this configuration. Check the business assets and permissions. No channel credentials were changed.",
      });
    }
    if (accounts.length === 0) {
      return back(redirectUri, {
        instagram_error: "Meta authorized no Instagram account for this app. In the Unified Login configuration, select the professional Instagram account and grant business_management. No channel credentials were changed.",
      });
    }
    if (accounts.length !== 1) {
      return back(redirectUri, {
        instagram_error: "This Unified Login configuration grants access to more than one Instagram account. Configure the login to select exactly one account for this channel, then reconnect. No channel credentials were changed.",
      });
    }

    const account = accounts[0];
    const remoteAccountId = account.id ? String(account.id) : "";
    const username = typeof account.username === "string" ? account.username : "";
    if (!remoteAccountId || !username) {
      return back(redirectUri, {
        instagram_error: "Meta did not return a complete Instagram account identity. No channel credentials were changed.",
      });
    }

    const expiresIn = Number(tokenPayload.expires_in);
    const tokenExpiresAt = Number.isFinite(expiresIn) && expiresIn > 0
      ? new Date(Date.now() + expiresIn * 1000).toISOString()
      : null;
    const linked = connectInstagramChannel({
      channelId,
      remoteAccountId,
      accessToken,
      tokenExpiresAt,
      instagramAuthMethod: "facebook_login",
      instagramUnifiedLogin: true,
    });
    if (linked === "already_linked") {
      return back(redirectUri, {
        instagram_error: "This Instagram account is already linked to another channel. No channel credentials were changed.",
      });
    }
    if (linked !== "connected") {
      return back(redirectUri, {
        instagram_error: "The selected Instagram channel no longer exists. No credentials were changed.",
      });
    }
    return back(redirectUri, {
      instagram_connected: String(channelId),
      instagram_username: username,
    });
  } catch {
    // Do not echo request errors: OAuth URLs may contain the authorization code or token.
    return back(redirectUri, {
      instagram_error: "Could not safely complete Unified Login with Meta. No channel credentials were changed.",
    });
  }
}

export async function GET(req: NextRequest) {
  const redirectUri = instagramRedirectUri();
  if (!redirectUri) {
    return NextResponse.json(
      { error: "Instagram OAuth callback URL is not configured on the server." },
      { status: 503 },
    );
  }
  const code = req.nextUrl.searchParams.get("code");
  const state = req.nextUrl.searchParams.get("state");
  const denied = req.nextUrl.searchParams.get("error");
  const expectedState = req.cookies.get(STATE_COOKIE)?.value;
  const targetId = Number(req.cookies.get(CHANNEL_COOKIE)?.value);
  const oauthMode = req.cookies.get(MODE_COOKIE)?.value ?? "instagram_login";

  // Validate the browser-bound state before handling either success or denial.
  // A forged error callback must not clear cookies belonging to a real OAuth attempt.
  if (!state || !expectedState || state !== expectedState) {
    return back(
      redirectUri,
      {
        instagram_error: "The Instagram sign-in session expired or did not match this browser. Try again.",
      },
      false,
    );
  }

  if (denied) {
    return back(redirectUri, {
      instagram_error: "Instagram authorization was declined or canceled. No channel credentials were changed.",
    });
  }

  if (!code || !Number.isSafeInteger(targetId) || targetId <= 0) {
    return back(redirectUri, {
      instagram_error: "The Instagram sign-in session expired or did not match this browser. Try again.",
    });
  }

  const target = getChannel(targetId);
  if (!target || target.platform !== "instagram") {
    return back(redirectUri, {
      instagram_error: "The selected Instagram channel no longer exists. No credentials were changed.",
    });
  }

  if (oauthMode === "unified_login") {
    return finishUnifiedLogin(redirectUri, targetId, code);
  }
  if (oauthMode !== "instagram_login") {
    return back(redirectUri, {
      instagram_error: "The Instagram sign-in mode is invalid. Start the connection again.",
    });
  }

  const { clientId, clientSecret } = instagramCredentials();
  if (!clientId || !clientSecret) {
    return back(redirectUri, {
      instagram_error: "Instagram OAuth credentials are not configured on the server.",
    });
  }

  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);

  try {
    // Meta's code exchange is a form POST to api.instagram.com. FormData lets fetch set
    // the multipart boundary correctly; the app secret and code never enter the browser.
    const form = new FormData();
    form.set("client_id", clientId);
    form.set("client_secret", clientSecret);
    form.set("grant_type", "authorization_code");
    form.set("redirect_uri", redirectUri);
    form.set("code", code);

    const shortResponse = await fetch("https://api.instagram.com/oauth/access_token", {
      method: "POST",
      body: form,
      signal: timeout,
      cache: "no-store",
    });
    const shortPayload = await readJson(shortResponse);
    // Meta integrations have returned both the documented flat token object and a
    // wrapped { data: [...] } shape. Accept either only when it identifies exactly one
    // token; reject mixed/ambiguous payloads rather than guessing which credential won.
    const shortTokenRows = Array.isArray(shortPayload?.data)
      ? shortPayload.data
      : shortPayload?.access_token
        ? [shortPayload]
        : [];
    const shortToken =
      shortTokenRows.length === 1 &&
      !(Array.isArray(shortPayload?.data) && shortPayload.access_token)
        ? shortTokenRows[0]
        : null;
    if (!shortResponse.ok || !shortToken?.access_token) {
      return back(redirectUri, {
        instagram_error: "Meta could not exchange the Instagram authorization code. Check the registered callback URL and app settings, then try again.",
      });
    }

    const granted = grantedPermissions(shortToken.permissions);
    const missingPermissions = INSTAGRAM_SCOPES.filter((permission) => !granted.has(permission));
    if (missingPermissions.length > 0) {
      return back(redirectUri, {
        instagram_error: "Instagram did not grant all required permissions. Missing: " + missingPermissions.join(", ") + ". Review the requested permissions in Meta and reconnect. No channel credentials were changed.",
      });
    }

    // Exchange for the long-lived token. This endpoint is intentionally unversioned.
    const longUrl = new URL("/access_token", INSTAGRAM_GRAPH_BASE);
    longUrl.searchParams.set("grant_type", "ig_exchange_token");
    longUrl.searchParams.set("client_secret", clientSecret);
    longUrl.searchParams.set("access_token", shortToken.access_token);

    const longResponse = await fetch(longUrl, {
      method: "GET",
      signal: timeout,
      cache: "no-store",
    });
    const longToken = await readJson(longResponse);
    if (!longResponse.ok || !longToken?.access_token) {
      return back(redirectUri, {
        instagram_error: "Meta did not issue a long-lived Instagram token. No channel credentials were changed.",
      });
    }

    // The worker publishes using the professional account id (user_id), not the
    // app-scoped id. Require that exact field instead of guessing from another id.
    const identityUrl = new URL(`/${config.graphVersion}/me`, INSTAGRAM_GRAPH_BASE);
    identityUrl.searchParams.set("fields", "user_id,username");
    identityUrl.searchParams.set("access_token", longToken.access_token);

    const identityResponse = await fetch(identityUrl, {
      method: "GET",
      signal: timeout,
      cache: "no-store",
    });
    const identityPayload = await readJson(identityResponse);
    // /me is a node lookup and may be returned as a flat object; some API
    // integrations wrap it in data. Accept one unambiguous identity only.
    const identityRows = Array.isArray(identityPayload?.data)
      ? identityPayload.data
      : identityPayload?.user_id
        ? [identityPayload]
        : [];
    const identity =
      identityRows.length === 1 &&
      !(Array.isArray(identityPayload?.data) && identityPayload.user_id)
        ? identityRows[0]
        : null;
    const remoteAccountId = identity?.user_id ? String(identity.user_id) : "";
    if (!identityResponse.ok || !remoteAccountId || !identity?.username) {
      return back(redirectUri, {
        instagram_error: "The Instagram token was issued, but Meta could not verify the professional account identity. No channel credentials were changed.",
      });
    }

    const expiresIn = Number(longToken.expires_in);
    if (!Number.isFinite(expiresIn) || expiresIn <= 0) {
      return back(redirectUri, {
        instagram_error: "Meta returned an invalid token expiry. No channel credentials were changed.",
      });
    }

    // Atomically update the explicitly selected row and guard against the same IG
    // account being attached to a second channel by concurrent OAuth callbacks.
    const linked = connectInstagramChannel({
      channelId: targetId,
      remoteAccountId,
      accessToken: longToken.access_token,
      tokenExpiresAt: new Date(Date.now() + expiresIn * 1000).toISOString(),
      instagramAuthMethod: "instagram_login",
    });
    if (linked === "already_linked") {
      return back(redirectUri, {
        instagram_error: "This Instagram account is already linked to another channel. No channel credentials were changed.",
      });
    }
    if (linked !== "connected") {
      return back(redirectUri, {
        instagram_error: "The selected Instagram channel no longer exists. No credentials were changed.",
      });
    }

    return back(redirectUri, {
      instagram_connected: String(targetId),
      instagram_username: identity.username,
    });
  } catch {
    // Never echo fetch errors: request URLs/bodies may contain OAuth credentials.
    return back(redirectUri, {
      instagram_error: "Could not safely complete the connection with Meta. No channel credentials were changed.",
    });
  }
}
