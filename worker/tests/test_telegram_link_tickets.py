from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from worker.telegram_link_tickets import (
    claim_link_ticket,
    create_link_ticket,
    finish_link_ticket,
    notify_link_ticket_results,
)


def test_ticket_is_bound_to_telegram_identity_not_channel(conn):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(
        conn, chat_id="chat-1", user_id="user-1", platform="instagram", now=now,
    )
    row = conn.execute("SELECT * FROM telegram_account_link_tickets").fetchone()
    assert row["telegram_chat_id"] == "chat-1"
    assert row["telegram_user_id"] == "user-1"
    assert row["platform"] == "instagram"
    assert "channel_id" not in row.keys()
    assert row["ticket_hash"] != ticket.token
    assert len(row["ticket_hash"]) == 64
    assert ticket.expires_at == (now + timedelta(minutes=10)).isoformat()


def test_ticket_can_be_claimed_only_once(conn):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(conn, chat_id="chat-2", user_id="user-2", now=now)
    claimed = claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=1))
    assert claimed is not None
    assert claimed.chat_id == "chat-2"
    assert claimed.user_id == "user-2"
    assert claimed.platform == "instagram"
    assert claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=2)) is None


def test_expired_ticket_cannot_be_claimed(conn):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(conn, chat_id="chat-3", user_id="user-3", now=now)
    assert claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=10)) is None


def test_ticket_rejects_non_instagram_linking(conn):
    with pytest.raises(ValueError, match="Only Instagram"):
        create_link_ticket(
            conn, chat_id="chat-4", user_id="user-4", platform="facebook",
        )


def test_ticket_completion_is_safe_and_single_transition(conn):
    ticket = create_link_ticket(conn, chat_id="chat-5", user_id="user-5")
    assert claim_link_ticket(conn, ticket.token) is not None
    assert finish_link_ticket(
        conn, token=ticket.token, success=True,
        account_name="sample", account_id="ig-100",
    )
    assert not finish_link_ticket(conn, token=ticket.token, success=False)
    row = conn.execute(
        "SELECT status, result_account_name, result_account_id, failure_reason "
        "FROM telegram_account_link_tickets"
    ).fetchone()
    assert tuple(row) == ("connected", "sample", "ig-100", None)


class _TelegramRecorder:
    def __init__(self):
        self.messages = []

    def send_message(self, token, chat_id, message):
        self.messages.append((token, chat_id, message))


def test_failed_oauth_result_is_delivered_to_originating_chat(conn):
    ticket = create_link_ticket(conn, chat_id="chat-fail", user_id="user-fail")
    assert claim_link_ticket(conn, ticket.token) is not None
    assert finish_link_ticket(
        conn, token=ticket.token, success=False,
        failure_reason="تم إلغاء تسجيل الدخول.",
    )
    client = _TelegramRecorder()
    delivered = notify_link_ticket_results(
        conn, SimpleNamespace(telegram_bot_token="bot-token"), client,
    )
    assert delivered == 1
    assert client.messages[0][0:2] == ("bot-token", "chat-fail")
    assert "تم إلغاء تسجيل الدخول" in client.messages[0][2]
    row = conn.execute("SELECT notified_at FROM telegram_account_link_tickets").fetchone()
    assert row["notified_at"] is not None


def test_abandoned_meta_login_expires_and_notifies_chat(conn):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    ticket = create_link_ticket(
        conn, chat_id="chat-expired", user_id="user-expired", now=now,
    )
    assert claim_link_ticket(conn, ticket.token, now=now + timedelta(minutes=1)) is not None
    conn.execute(
        "UPDATE telegram_account_link_tickets SET expires_at=?",
        ((now + timedelta(minutes=11)).isoformat(),),
    )
    client = _TelegramRecorder()
    delivered = notify_link_ticket_results(
        conn, SimpleNamespace(telegram_bot_token="bot-token"), client,
    )
    assert delivered == 1
    assert client.messages[0][1] == "chat-expired"
    assert "انتهت مهلة" in client.messages[0][2]
