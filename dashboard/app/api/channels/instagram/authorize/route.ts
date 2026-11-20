import { NextRequest, NextResponse } from "next/server";
import { getChannel } from "@/lib/queries";
import { instagramCredentials, instagramRedirectUri } from "@/lib/config";
import {
  createInstagramOAuthState,
  instagramAuthorizeUrl,
} from "@/lib/instagram-oauth";

export const runtime = "nodejs";

const STATE_COOKIE = "instagram_oauth_state";
const CHANNEL_COOKIE = "instagram_oauth_channel_id";
const COOKIE_TTL_SECONDS = 600;

function channelPage(redirectUri: string, message: string) {
  const url = new URL("/channels", new URL(redirectUri).origin);
  url.searchParams.set("instagram_error", message);
  return NextResponse.redirect(url);
}

/**
 * Start Instagram Business Login for an EXISTING Instagram channel.
 *
 * The channel id is kept in a short-lived httpOnly cookie alongside the random state.
 * The callback can therefore update only the channel the owner explicitly selected,
 * preserving its label, timezone, queue and history instead of creating a duplicate.
 */
export async function POST(req: NextRequest) {
  // Starting an account-linking flow changes browser-bound OAuth state. Require a
  // same-origin POST (enforced by proxy.ts) rather than a cross-site GET link, so an
  // external page cannot silently start linking an Instagram account to a channel.
  const form = await req.formData().catch(() => null);
  if (!form) {
    return NextResponse.json({ error: "Invalid Instagram connection request." }, { status: 400 });
  }
  // Use a fixed, operator-configured callback URL. Request Host and forwarded-host
  // headers must never influence the redirect_uri sent to Meta.
  const redirectUri = instagramRedirectUri();
  if (!redirectUri) {
    return NextResponse.json(
      { error: "Set INSTAGRAM_REDIRECT_URI to the exact public Instagram callback URL before connecting." },
      { status: 503 },
    );
  }

  const channelId = Number(form.get("channel_id"));
  if (!Number.isSafeInteger(channelId) || channelId <= 0) {
    return channelPage(redirectUri, "Choose an existing Instagram channel before connecting.");
  }

  const channel = getChannel(channelId);
  if (!channel || channel.platform !== "instagram") {
    return channelPage(redirectUri, "The selected Instagram channel was not found.");
  }

  const { clientId, clientSecret } = instagramCredentials();
  if (!clientId || !clientSecret) {
    return channelPage(
      redirectUri,
      "Meta Instagram Login credentials are missing. Configure INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET on the server first.",
    );
  }

  const state = createInstagramOAuthState();
  const response = NextResponse.redirect(
    instagramAuthorizeUrl({ clientId, redirectUri, state }),
  );
  response.headers.set("Cache-Control", "no-store");

  const cookieOptions = {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: COOKIE_TTL_SECONDS,
  };
  response.cookies.set(STATE_COOKIE, state, cookieOptions);
  response.cookies.set(CHANNEL_COOKIE, String(channelId), cookieOptions);
  return response;
}
