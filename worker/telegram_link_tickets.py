"""Single-use, Telegram-bound account-link ticket primitives.

The raw bearer ticket is returned only to the caller that sends it to Telegram.
SQLite stores only its SHA-256 digest. Tickets are scoped to one allow-listed
Telegram identity, one existing channel, one platform, and a short expiry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3


TICKET_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class LinkTicket:
    token: str
    expires_at: str


@dataclass(frozen=True)
class ClaimedLinkTicket:
    chat_id: str
    user_id: str
    channel_id: int
    platform: str


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_link_ticket(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    user_id: str,
    channel_id: int,
    platform: str,
    now: datetime | None = None,
) -> LinkTicket:
    """Create a 10-minute ticket for a verified, existing channel."""
    chat_id, user_id = str(chat_id).strip(), str(user_id).strip()
    if not chat_id or not user_id:
        raise ValueError("Telegram chat and user IDs are required.")
    if platform != "instagram":
        raise ValueError("Only Instagram account linking is supported.")
    channel = conn.execute(
        "SELECT id, platform FROM channels WHERE id = ?", (int(channel_id),)
    ).fetchone()
    if channel is None or channel["platform"] != platform:
        raise ValueError("The selected channel does not match the requested platform.")

    current = now or datetime.now(timezone.utc)
    created_at = _iso(current)
    expires_at = _iso(current + TICKET_TTL)
    token = secrets.token_urlsafe(32)
    digest = _digest(token)

    # Expire old pending tickets for this exact chat/channel before issuing a new one.
    conn.execute(
        """UPDATE telegram_account_link_tickets
              SET status = 'expired'
            WHERE telegram_chat_id = ? AND channel_id = ?
              AND status = 'pending' AND expires_at <= ?""",
        (chat_id, int(channel_id), created_at),
    )
    conn.execute(
        """INSERT INTO telegram_account_link_tickets
           (ticket_hash, telegram_chat_id, telegram_user_id, channel_id,
            platform, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (digest, chat_id, user_id, int(channel_id), platform, created_at, expires_at),
    )
    return LinkTicket(token=token, expires_at=expires_at)


def claim_link_ticket(
    conn: sqlite3.Connection,
    token: str,
    *,
    now: datetime | None = None,
) -> ClaimedLinkTicket | None:
    """Atomically redeem a ticket once; never return or persist the raw token."""
    if not isinstance(token, str) or len(token) < 32 or len(token) > 128:
        return None
    current = _iso(now or datetime.now(timezone.utc))
    row = conn.execute(
        """SELECT id, telegram_chat_id, telegram_user_id, channel_id, platform
             FROM telegram_account_link_tickets
            WHERE ticket_hash = ? AND status = 'pending'
              AND consumed_at IS NULL AND expires_at > ?""",
        (_digest(token), current),
    ).fetchone()
    if row is None:
        return None
    changed = conn.execute(
        """UPDATE telegram_account_link_tickets
              SET consumed_at = ?
            WHERE id = ? AND status = 'pending'
              AND consumed_at IS NULL AND expires_at > ?""",
        (current, row["id"], current),
    ).rowcount
    if changed != 1:
        return None
    return ClaimedLinkTicket(
        chat_id=str(row["telegram_chat_id"]),
        user_id=str(row["telegram_user_id"]),
        channel_id=int(row["channel_id"]),
        platform=str(row["platform"]),
    )


def finish_link_ticket(
    conn: sqlite3.Connection,
    *,
    token: str,
    success: bool,
    account_name: str | None = None,
    account_id: str | None = None,
    failure_reason: str | None = None,
) -> bool:
    """Record a safe, user-displayable outcome for the bot's status poller."""
    status = "connected" if success else "failed"
    changed = conn.execute(
        """UPDATE telegram_account_link_tickets
              SET status = ?, result_account_name = ?, result_account_id = ?,
                  failure_reason = ?
            WHERE ticket_hash = ? AND status = 'pending'
              AND consumed_at IS NOT NULL""",
        (
            status,
            (account_name or "")[:160] or None,
            (account_id or "")[:160] or None,
            (failure_reason or "")[:300] or None,
            _digest(token),
        ),
    ).rowcount
    return changed == 1


def notify_link_ticket_results(conn, config, client, *, logger=None, limit: int = 10) -> int:
    """Deliver completed Instagram-link outcomes to their originating Telegram chats."""
    token = str(getattr(config, "telegram_bot_token", "") or "")
    if not token:
        return 0
    rows = conn.execute(
        """SELECT id, telegram_chat_id, status, result_account_name,
                  result_account_id, failure_reason
             FROM telegram_account_link_tickets
            WHERE status IN ('connected', 'failed') AND notified_at IS NULL
            ORDER BY id LIMIT ?""",
        (max(1, min(int(limit), 50)),),
    ).fetchall()
    delivered = 0
    for row in rows:
        if row["status"] == "connected":
            message = (
                "✅ تم ربط حساب Instagram بنجاح.\\n"
                f"الحساب: @{row['result_account_name'] or 'غير معروف'}\\n"
                f"المعرّف: {row['result_account_id'] or 'غير متوفر'}"
            )
        else:
            message = "❌ تعذر ربط حساب Instagram. " + str(row["failure_reason"] or "افتح الحسابات وحاول مجدداً.")
        try:
            client.send_message(token, str(row["telegram_chat_id"]), message[:3500])
        except Exception as exc:
            if logger:
                logger.warning("Could not deliver Instagram link result for ticket %s: %s", row["id"], exc)
            continue
        changed = conn.execute(
            "UPDATE telegram_account_link_tickets SET notified_at=? WHERE id=? AND notified_at IS NULL",
            (_iso(datetime.now(timezone.utc)), row["id"]),
        ).rowcount
        delivered += int(changed == 1)
    return delivered
