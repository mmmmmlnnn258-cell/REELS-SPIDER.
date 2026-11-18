import { NextRequest, NextResponse } from "next/server";
import { connectInstagramChannel, getChannel } from "@/lib/queries";
import { config, instagramCredentials } from "@/lib/config";
import { instagramCallbackUri } from "@/lib/instagram-oauth";

export const runtime = "nodejs";

const STATE_COOKIE = "instagram_oauth_state";
const CHANNEL_COOKIE = "instagram_oauth_channel_id";
const REQUEST_TIMEOUT_MS = 20_000;

type MetaPayload = {
  access_token?: string;
  token_type?: string;
  expires_in?: number;
  user_id?: number | string;
  username?: string;
  error?: unknown;
};

function back(req: NextRequest, params: Record<string, string>) {
  const url = new URL("/channels", req.nextUrl.origin);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  const response = NextResponse.redirect(url);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.delete(STATE_COOKIE);
  response.cookies.delete(CHANNEL_COOKIE);
  return response;
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
export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code");
  const state = req.nextUrl.searchParams.get("state");
  const denied = req.nextUrl.searchParams.get("error");
  const expectedState = req.cookies.get(STATE_COOKIE)?.value;
  const targetId = Number(req.cookies.get(CHANNEL_COOKIE)?.value);

  if (denied) {
    return back(req, {
      instagram_error: "Instagram authorization was declined or canceled. No channel credentials were changed.",
    });
  }

  if (
    !code ||
    !state ||
    !expectedState ||
    state !== expectedState ||
    !Number.isSafeInteger(targetId) ||
    targetId <= 0
  ) {
    return back(req, {
      instagram_error: "The Instagram sign-in session expired or did not match this browser. Try again.",
    });
  }

  const target = getChannel(targetId);
  if (!target || target.platform !== "instagram") {
    return back(req, {
      instagram_error: "The selected Instagram channel no longer exists. No credentials were changed.",
    });
  }

  const { clientId, clientSecret } = instagramCredentials();
  if (!clientId || !clientSecret) {
    return back(req, {
      instagram_error: "Instagram OAuth credentials are not configured on the server.",
    });
  }

  const redirectUri = instagramCallbackUri(req.nextUrl.origin);
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
    const shortToken = await readJson(shortResponse);
    if (!shortResponse.ok || !shortToken?.access_token) {
      return back(req, {
        instagram_error: "Meta could not exchange the Instagram authorization code. Check the registered callback URL and app settings, then try again.",
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
      return back(req, {
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
    const identity = await readJson(identityResponse);
    const remoteAccountId = identity?.user_id ? String(identity.user_id) : "";
    if (!identityResponse.ok || !remoteAccountId || !identity?.username) {
      return back(req, {
        instagram_error: "The Instagram token was issued, but Meta could not verify the professional account identity. No channel credentials were changed.",
      });
    }

    const expiresIn = Number(longToken.expires_in);
    if (!Number.isFinite(expiresIn) || expiresIn <= 0) {
      return back(req, {
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
      return back(req, {
        instagram_error: "This Instagram account is already linked to another channel. No channel credentials were changed.",
      });
    }
    if (linked !== "connected") {
      return back(req, {
        instagram_error: "The selected Instagram channel no longer exists. No credentials were changed.",
      });
    }

    return back(req, {
      instagram_connected: String(targetId),
      instagram_username: identity.username,
    });
  } catch {
    // Never echo fetch errors: request URLs/bodies may contain OAuth credentials.
    return back(req, {
      instagram_error: "Could not safely complete the connection with Meta. No channel credentials were changed.",
    });
  }
}
