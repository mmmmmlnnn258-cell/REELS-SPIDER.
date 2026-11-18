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
export function GET(req: NextRequest) {
  // Use a fixed, operator-configured callback URL. Request Host and forwarded-host
  // headers must never influence the redirect_uri sent to Meta.
  const redirectUri = instagramRedirectUri();
  if (!redirectUri) {
    return NextResponse.json(
      { error: "Set INSTAGRAM_REDIRECT_URI to the exact public Instagram callback URL before connecting." },
      { status: 503 },
    );
  }

  const channelId = Number(req.nextUrl.searchParams.get("channel_id"));
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
      "Meta Instagram Login credentials are missing. Configure META_APP_ID and INSTAGRAM_APP_SECRET on the server first.",
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
