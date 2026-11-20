from datetime import datetime, timedelta, timezone

import pytest

from worker.telegram_link_tickets import (
    claim_link_ticket,
    create_link_ticket,
    finish_link_ticket,
)


def _channel(conn, platform="instagram"):
    return int(conn.execute(
        """INSERT INTO channels(platform, account_name, timezone)
           VALUES (?, ?, 'Asia/Aden')""",
        (platform, f"قناة {platform}"),
    ).lastrowid)


def test_ticket_is_bound_to_chat_user_channel_and_platform(conn):
    channel_id = _channel(conn)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(
        conn, chat_id="chat-1", user_id="user-1",
        channel_id=channel_id, platform="instagram", now=now,
    )
    row = conn.execute(
        "SELECT * FROM telegram_account_link_tickets"
    ).fetchone()
    assert row["telegram_chat_id"] == "chat-1"
    assert row["telegram_user_id"] == "user-1"
    assert row["channel_id"] == channel_id
    assert row["platform"] == "instagram"
    assert row["ticket_hash"] != ticket.token
    assert len(row["ticket_hash"]) == 64
    assert ticket.expires_at == (now + timedelta(minutes=10)).isoformat()


def test_ticket_can_be_claimed_only_once(conn):
    channel_id = _channel(conn)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(
        conn, chat_id="chat-2", user_id="user-2",
        channel_id=channel_id, platform="instagram", now=now,
    )
    claimed = claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=1))
    assert claimed is not None
    assert claimed.chat_id == "chat-2"
    assert claimed.user_id == "user-2"
    assert claimed.channel_id == channel_id
    assert claimed.platform == "instagram"
    assert claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=2)) is None


def test_expired_ticket_cannot_be_claimed(conn):
    channel_id = _channel(conn)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(
        conn, chat_id="chat-3", user_id="user-3",
        channel_id=channel_id, platform="instagram", now=now,
    )
    assert claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=10)) is None


def test_ticket_rejects_facebook_linking(conn):
    channel_id = _channel(conn, "instagram")
    with pytest.raises(ValueError, match="Only Instagram"):
        create_link_ticket(
            conn, chat_id="chat-4", user_id="user-4",
            channel_id=channel_id, platform="facebook",
        )


def test_ticket_completion_is_safe_and_single_transition(conn):
    channel_id = _channel(conn)
    ticket = create_link_ticket(
        conn, chat_id="chat-5", user_id="user-5",
        channel_id=channel_id, platform="instagram",
    )
    assert claim_link_ticket(conn, ticket.token) is not None
    assert finish_link_ticket(
        conn, token=ticket.token, success=True,
        account_name="@sample", account_id="ig-100",
    )
    assert not finish_link_ticket(conn, token=ticket.token, success=False)
    row = conn.execute(
        "SELECT status, result_account_name, result_account_id, failure_reason "
        "FROM telegram_account_link_tickets"
    ).fetchone()
    assert tuple(row) == ("connected", "@sample", "ig-100", None)
