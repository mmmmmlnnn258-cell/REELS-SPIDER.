import "server-only";

const API_BASE = "https://zernio.com/api/v1";

export class ZernioApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ZernioApiError";
  }
}

function apiKey(): string {
  const value = (process.env.ZERNIO_API_KEY || "").trim();
  if (!value) throw new ZernioApiError("ZERNIO_API_KEY is not configured.");
  return value;
}

async function request(
  path: string,
  init: { method?: string; body?: string; headers?: Record<string, string> } = {},
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetch(API_BASE + path, {
      method: init.method || "GET",
      body: init.body,
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${apiKey()}`,
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(init.headers || {}),
      },
      signal: AbortSignal.timeout(20_000),
    });
  } catch {
    throw new ZernioApiError("Could not reach Zernio. Check the network and try again.");
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Zernio rejected the API key. Check ZERNIO_API_KEY.",
      402: "The current Zernio plan does not allow this operation.",
      403: "Zernio denied access to this profile.",
      404: "The requested Zernio resource was not found.",
      409: "A matching Zernio resource already exists.",
      429: "Zernio rate limit reached. Try again later.",
    };
    throw new ZernioApiError(messages[response.status] || `Zernio request failed (HTTP ${response.status}).`);
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ZernioApiError("Zernio returned an invalid response.");
  }
  return payload as Record<string, unknown>;
}

export async function ensureZernioProfile(): Promise<string> {
  const name = "BLACK-SPIDER";
  const query = new URLSearchParams({ name });
  const listed = await request(`/profiles?${query.toString()}`);
  const profiles = Array.isArray(listed.profiles) ? listed.profiles : [];
  const existing = profiles.find((item) => item && typeof item === "object" &&
    typeof ((item as Record<string, unknown>)._id || (item as Record<string, unknown>).id) === "string") as
      Record<string, unknown> | undefined;
  const existingId = existing?._id || existing?.id;
  if (typeof existingId === "string" && existingId) return existingId;

  try {
    const created = await request("/profiles", {
      method: "POST",
      body: JSON.stringify({ name, description: "BLACK SPIDER SocialScheduler" }),
      headers: { "Idempotency-Key": "black-spider-profile-v1" },
    });
    const profile = (created.profile || {}) as Record<string, unknown>;
    const id = profile._id || profile.id;
    if (typeof id !== "string" || !id) throw new ZernioApiError("Zernio did not return a profile ID.");
    return id;
  } catch (error) {
    // Concurrent requests may race to create the same unique profile name.
    if (!(error instanceof ZernioApiError) || !error.message.includes("already exists")) throw error;
    const retry = await request(`/profiles?${query.toString()}`);
    const rows = Array.isArray(retry.profiles) ? retry.profiles : [];
    const match = rows.find((item) => item && typeof item === "object") as Record<string, unknown> | undefined;
    const id = match?._id || match?.id;
    if (typeof id !== "string" || !id) throw error;
    return id;
  }
}

export async function createInstagramConnectUrl(profileId: string, redirectUrl: string): Promise<string> {
  if (!profileId || !redirectUrl.startsWith("https://")) {
    throw new ZernioApiError("A profile ID and HTTPS callback URL are required.");
  }
  const query = new URLSearchParams({ profileId, redirect_url: redirectUrl });
  const result = await request(`/connect/instagram?${query.toString()}`);
  if (typeof result.authUrl !== "string" || !result.authUrl.startsWith("https://")) {
    throw new ZernioApiError("Zernio did not return a valid Instagram authorization URL.");
  }
  return result.authUrl;
}

export async function getInstagramAccount(profileId: string, accountId: string) {
  const query = new URLSearchParams({ profileId });
  const result = await request(`/accounts?${query.toString()}`);
  const accounts = Array.isArray(result.accounts) ? result.accounts : [];
  const account = accounts.find((item) => {
    if (!item || typeof item !== "object") return false;
    const row = item as Record<string, unknown>;
    const rawProfile = row.profileId;
    const rowProfileId = typeof rawProfile === "string" ? rawProfile :
      rawProfile && typeof rawProfile === "object"
        ? String((rawProfile as Record<string, unknown>)._id || (rawProfile as Record<string, unknown>).id || "")
        : "";
    return (row._id === accountId || row.accountId === accountId) &&
      row.platform === "instagram" && rowProfileId === profileId && row.isActive !== false;
  }) as Record<string, unknown> | undefined;
  if (!account) throw new ZernioApiError("The Instagram account was not found in the expected Zernio profile.");
  const username = String(account.username || account.displayName || "").trim();
  if (!username) throw new ZernioApiError("Zernio did not return an Instagram username.");
  return {
    accountId,
    username,
    platform: "instagram" as const,
  };
}
