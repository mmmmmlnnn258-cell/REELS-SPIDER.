"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { accountIdLabel, usesAccountId } from "@/lib/platforms";

// Platforms whose token expiry the worker tracks (worker/token_upkeep.py).
const TRACKS_EXPIRY = new Set(["instagram", "threads", "facebook"]);

/**
 * Update a channel's credentials (IG user id + access token). Tokens expire / get
 * regenerated, so editing them is a routine need. The token is write-only here — we
 * never render the stored value back.
 */
export function ChannelCredentials({
  channelId,
  platform,
  remoteAccountId,
  instagramAuthMethod,
}: {
  channelId: number;
  platform: string;
  remoteAccountId: string | null;
  instagramAuthMethod: "legacy" | "facebook_login" | "instagram_login";
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [accountId, setAccountId] = useState(remoteAccountId ?? "");
  const [token, setToken] = useState("");
  const [authMethod, setAuthMethod] = useState(instagramAuthMethod);
  const [pending, startT] = useTransition();
  const [msg, setMsg] = useState<string | null>(null);

  async function save() {
    setMsg(null);
    const body: Record<string, string> = { remote_account_id: accountId.trim() };
    if (platform === "instagram") body.instagram_auth_method = authMethod;
    // Only send the token if they typed a new one (leave it untouched otherwise).
    if (token.trim()) body.access_token = token.trim();
    const res = await fetch(`/api/channels/${channelId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      setMsg("Could not save.");
      return;
    }
    setToken("");
    // For Meta channels the worker checks a newly saved token on its next cycle and
    // fills in the expiry on the card, so the card is the confirmation. Other
    // platforms have no expiry to show, so they keep the preflight pointer.
    setMsg(
      TRACKS_EXPIRY.has(platform)
        ? "Saved. The worker checks it within a minute — refresh to see its expiry."
        : "Saved — run the preflight check to verify."
    );
    startT(() => router.refresh());
  }

  const field =
    "w-full rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm text-ink focus:border-brand";

  return (
    <div className="mt-3 rounded-lg border border-border bg-surface-sunken/40 p-3">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="text-xs font-medium text-ink-soft">Credentials</span>
        {/* Says "Edit", not "Update token" — this panel also edits the account id, and
            labelling it by the token alone hides that. A wrong account id is a real and
            easy mistake (the Threads id is not the Instagram one). */}
        <span className="text-xs text-muted">{open ? "Hide" : "Edit"}</span>
      </button>

      {open ? (
        <div className="mt-3 space-y-2.5">
          {platform === "instagram" ? (
            <label className="block text-xs text-ink-soft">
              <span className="mb-1 block">Instagram login method</span>
              <select
                className={field}
                value={authMethod}
                onChange={(e) =>
                  setAuthMethod(e.target.value as typeof authMethod)
                }
              >
                <option value="legacy">Legacy — follow META_GRAPH_BASE</option>
                <option value="facebook_login">Facebook Login — Page token</option>
                <option value="instagram_login">Instagram Login — Instagram user token</option>
              </select>
              <span className="mt-1 block text-[11px] text-muted">
                Changing this changes the API host and token lifecycle. Reconnect with Instagram OAuth when selecting Instagram Login.
              </span>
            </label>
          ) : null}
          {usesAccountId(platform) ? (
            <label className="block text-xs text-ink-soft">
              <span className="mb-1 block">
                {accountIdLabel(platform)}
              </span>
              <input
                className={field}
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
                placeholder="1784140000..."
              />
            </label>
          ) : null}
          <label className="block text-xs text-ink-soft">
            <span className="mb-1 block">
              {usesAccountId(platform) ? (
                <>
                  New access token{" "}
                  {/* The "IGAA…" prefix is Instagram's; Threads and Page tokens look different,
                      so don't tell someone their valid token looks wrong. */}
                  <span className="text-faint">(long — leave blank to keep the current one)</span>
                </>
              ) : (
                <>
                  New webhook URL{" "}
                  <span className="text-faint">(the whole credential — leave blank to keep the current one)</span>
                </>
              )}
            </span>
            <input
              className={field}
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={
                usesAccountId(platform)
                  ? "Paste the freshly generated token"
                  : "Paste the full Discord webhook URL"
              }
            />
          </label>
          <div className="flex items-center gap-3">
            <button
              onClick={save}
              disabled={pending}
              className="rounded-md bg-brand px-3 py-1.5 text-xs font-semibold text-on-brand hover:bg-brand-ink disabled:opacity-50"
            >
              {pending ? "Saving…" : "Save credentials"}
            </button>
            {msg ? <span className="text-xs text-status-posted">{msg}</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
