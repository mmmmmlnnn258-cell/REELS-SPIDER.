from dataclasses import replace
from datetime import datetime, timedelta, timezone

from worker.telegram_control import process_control_update


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.answered = []

    def send_message(self, token, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return {"message_id": len(self.messages)}

    def answer_callback_query(self, token, callback_query_id, text=None):
        self.answered.append((callback_query_id, text))
        return {}


class FakeGraph:
    def __init__(self, fail=False):
        self.fail = fail
        self.base = "https://graph.instagram.com/v25.0"
        self.sent = []
        self.last_inbound_at = datetime.now(timezone.utc).isoformat()
        self.conversations = [{
            "id": "9001", "updated_time": "2026-11-18T12:00:00+0000",
            "participants": {"data": [
                {"id": "ig-business", "username": "our_account"},
                {"id": "person-77", "username": "customer"},
            ]},
        }]

    def get_media_comments(self, media_id, token, limit=50, next_url=None):
        if self.fail:
            raise RuntimeError("instagram_manage_comments permission required")
        return ([{"id": "comment-1", "text": "السعر كم؟", "from": {"username": "customer"}}], None)

    def get_instagram_conversations(self, ig_user_id, token, limit=20):
        if self.fail:
            raise RuntimeError("instagram_manage_messages permission required")
        return self.conversations

    def get_instagram_conversation_messages(self, conversation_id, token):
        if self.fail:
            raise RuntimeError("permission denied")
        return ({"id": conversation_id, "participants": self.conversations[0]["participants"]},
                [{"id": "m1", "from": {"id": "person-77", "username": "customer"},
                  "message": "مرحبا", "created_time": self.last_inbound_at}])

    def send_instagram_direct_message(self, ig_user_id, recipient_id, message, token):
        if self.fail:
            raise RuntimeError("Meta rejected message")
        self.sent.append((ig_user_id, recipient_id, message))
        return {"message_id": "meta-msg-1"}


def cfg(config):
    return replace(config, telegram_bot_token="test-token",
                   telegram_allowed_chat_ids="7441755677")


def cb(update_id, data):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb-{update_id}", "data": data,
        "from": {"id": 7441755677},
        "message": {"message_id": 10, "chat": {"id": 7441755677, "type": "private"}},
    }}


def msg(update_id, text):
    return {"update_id": update_id, "message": {
        "text": text, "from": {"id": 7441755677},
        "chat": {"id": 7441755677, "type": "private"},
    }}


def connected_channel(conn, linked_page_id=None, auth_method="legacy"):
    return conn.execute(
        """INSERT INTO channels
           (platform,account_name,remote_account_id,linked_page_id,access_token,is_active,
            instagram_auth_method)
           VALUES('instagram','connected','ig-business',?,'test-access',1,?)""",
        (linked_page_id, auth_method),
    ).lastrowid


def test_main_menu_has_separate_comment_and_dm_buttons(conn, config):
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, msg(1, "/start"))
    buttons = [b for row in client.messages[0][2]["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == "bs:comments" for b in buttons)
    assert any(b["callback_data"] == "bs:dms" for b in buttons)


def test_comment_rule_can_be_opened_and_reply_text_edited(conn, config):
    channel = connected_channel(conn)
    rule = conn.execute(
        """INSERT INTO instagram_comment_rules(channel_id,media_id,keywords_json,
             public_reply_text,enabled) VALUES(?,?,?,?,1)""",
        (channel, "media-1", '["price"]', "Old reply"),
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(2, f"bs:comment:detail:{rule}"))
    buttons = client.messages[-1][2]["inline_keyboard"]
    assert any("bs:comment:edit:" in b["callback_data"] for row in buttons for b in row)
    process_control_update(conn, cfg(config), client, cb(3, f"bs:comment:edit:{rule}:public"))
    process_control_update(conn, cfg(config), client, msg(4, "New public reply"))
    assert conn.execute(
        "SELECT public_reply_text FROM instagram_comment_rules WHERE id=?", (rule,)
    ).fetchone()[0] == "New public reply"


def test_dm_reply_requires_preview_and_explicit_confirmation(conn, config):
    channel = connected_channel(conn)
    graph = FakeGraph()
    client = FakeTelegram()
    graph_for = lambda platform: graph
    process_control_update(conn, cfg(config), client, cb(10, f"bs:dm:view:{channel}:9001"), graph_for)
    assert "مرحبا" in client.messages[-1][1]
    process_control_update(conn, cfg(config), client, cb(11, "bs:dm:compose"), graph_for)
    process_control_update(conn, cfg(config), client, msg(12, "رد يدوي"), graph_for)
    row = conn.execute("SELECT id,status FROM telegram_dm_outbox").fetchone()
    assert row["status"] == "pending"
    assert graph.sent == []
    assert "لم تُرسل الرسالة بعد" in client.messages[-1][1] or "لم تُرسل" in client.messages[-1][1]
    process_control_update(conn, cfg(config), client, cb(13, f"bs:dm:send:{row['id']}"), graph_for)
    assert graph.sent == [("ig-business", "person-77", "رد يدوي")]
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (row["id"],)).fetchone()[0] == "sent"
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE event_type='رسالة خاصة يدوية'"
    ).fetchone()[0]


def test_dm_reply_is_blocked_after_instagram_24_hour_window(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()
    graph = FakeGraph()
    graph.last_inbound_at = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    client = FakeTelegram()

    process_control_update(conn, cfg(config), client, cb(19, f"bs:dm:send:{outbox}"), lambda _: graph)

    assert graph.sent == []
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "cancelled"
    assert "24 ساعة" in client.messages[-1][1]


def test_dm_confirmation_is_idempotent_and_never_resends(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()
    graph = FakeGraph()
    client = FakeTelegram()
    update = cb(20, f"bs:dm:send:{outbox}")
    process_control_update(conn, cfg(config), client, update, lambda _: graph)
    process_control_update(conn, cfg(config), client, update, lambda _: graph)
    assert len(graph.sent) == 1
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "sent"


def test_expired_dm_preview_cannot_be_confirmed(conn, config):
    channel = connected_channel(conn)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, expired, expired),
    ).lastrowid
    conn.commit()
    graph = FakeGraph()
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(204, f"bs:dm:send:{outbox}"), lambda _: graph)
    assert graph.sent == []
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "cancelled"
    assert "انتهت صلاحية المعاينة" in client.messages[-1][1]


def test_dm_cancel_marks_preview_final_and_old_confirm_cannot_send(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()
    graph = FakeGraph()
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(201, f"bs:dm:cancel:{outbox}"), lambda _: graph)
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "cancelled"
    process_control_update(conn, cfg(config), client, cb(202, f"bs:dm:send:{outbox}"), lambda _: graph)
    assert graph.sent == []
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "cancelled"


def test_menu_command_cancels_pending_dm_previews(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, msg(203, "/menu"))
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "cancelled"


def test_facebook_login_dm_uses_linked_page_id_for_send(conn, config):
    channel = connected_channel(conn, linked_page_id="page-123", auth_method="facebook_login")
    graph = FakeGraph()
    client = FakeTelegram()
    graph_for = lambda platform: graph
    process_control_update(conn, cfg(config), client, cb(205, f"bs:dm:view:{channel}:9001"), graph_for)
    process_control_update(conn, cfg(config), client, cb(206, "bs:dm:compose"), graph_for)
    process_control_update(conn, cfg(config), client, msg(207, "رد يدوي"), graph_for)
    outbox = conn.execute("SELECT id FROM telegram_dm_outbox").fetchone()["id"]
    process_control_update(conn, cfg(config), client, cb(208, f"bs:dm:send:{outbox}"), graph_for)
    assert graph.sent == [("page-123", "person-77", "رد يدوي")]


def test_instagram_login_method_uses_instagram_account_id_even_if_client_host_looks_facebook(conn, config):
    channel = connected_channel(conn, auth_method="instagram_login")
    graph = FakeGraph()
    graph.base = "https://graph.facebook.com/v25.0"
    client = FakeTelegram()
    graph_for = lambda row: graph
    process_control_update(conn, cfg(config), client, cb(250, f"bs:dm:view:{channel}:9001"), graph_for)
    process_control_update(conn, cfg(config), client, cb(251, "bs:dm:compose"), graph_for)
    process_control_update(conn, cfg(config), client, msg(252, "مرحبا"), graph_for)
    outbox = conn.execute("SELECT id FROM telegram_dm_outbox").fetchone()["id"]
    process_control_update(conn, cfg(config), client, cb(253, f"bs:dm:send:{outbox}"), graph_for)
    assert graph.sent == [("ig-business", "person-77", "مرحبا")]


def test_dm_meta_failure_is_marked_unknown_and_not_retried(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()
    graph = FakeGraph(fail=True)
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(21, f"bs:dm:send:{outbox}"), lambda _: graph)
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "unknown"
    assert "غير مؤكدة" in client.messages[-1][1]
    process_control_update(conn, cfg(config), client, cb(22, f"bs:dm:send:{outbox}"), lambda _: graph)
    assert graph.sent == []


def test_dm_success_without_meta_message_id_is_not_reported_as_sent(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'9001','person-77','hello','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()
    graph = FakeGraph()
    graph.send_instagram_direct_message = lambda *args: {}
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(209, f"bs:dm:send:{outbox}"), lambda _: graph)
    assert conn.execute("SELECT status FROM telegram_dm_outbox WHERE id=?", (outbox,)).fetchone()[0] == "unknown"
    assert "نتيجة الإرسال غير مؤكدة" in client.messages[-1][1]
    assert "أرسلت الرد بنجاح" not in client.messages[-1][1]


def test_dm_list_surfaces_meta_permission_requirements(conn, config):
    connected_channel(conn)
    client = FakeTelegram()
    graph = FakeGraph(fail=True)
    process_control_update(conn, cfg(config), client, cb(30, "bs:dms"), lambda _: graph)
    assert "instagram_business_manage_messages" in client.messages[-1][1]


def test_comment_live_view_reads_from_meta_without_sending_replies(conn, config):
    channel = connected_channel(conn)
    rule = conn.execute("INSERT INTO instagram_comment_rules(channel_id,media_id,enabled) VALUES(?,?,1)", (channel, "media-1")).lastrowid
    conn.commit()
    client = FakeTelegram()
    graph = FakeGraph()
    process_control_update(conn, cfg(config), client, cb(40, f"bs:comment:fetch:{rule}"), lambda _: graph)
    assert "السعر كم؟" in client.messages[-1][1]
    assert graph.sent == []


def test_dm_inbox_supports_opaque_meta_conversation_ids(conn, config):
    channel = connected_channel(conn)
    graph = FakeGraph()
    graph.conversations[0]["id"] = "t_opaque-conversation_abc-123"
    client = FakeTelegram()
    graph_for = lambda platform: graph

    process_control_update(conn, cfg(config), client, cb(300, "bs:dms"), graph_for)
    keyboard = client.messages[-1][2]["inline_keyboard"]
    callback_data = next(
        button["callback_data"]
        for row in keyboard for button in row
        if button["callback_data"].startswith("bs:dm:view:")
    )
    assert callback_data.count(":") == 3
    ref_id = callback_data.rsplit(":", 1)[1]
    assert ref_id.isdigit()
    stored = conn.execute(
        "SELECT conversation_id,channel_id,chat_id FROM telegram_dm_conversation_refs WHERE id=?",
        (int(ref_id),),
    ).fetchone()
    assert stored["conversation_id"] == "t_opaque-conversation_abc-123"
    assert stored["channel_id"] == channel
    assert stored["chat_id"] == "7441755677"

    process_control_update(conn, cfg(config), client, cb(301, callback_data), graph_for)
    assert "مرحبا" in client.messages[-1][1]
    assert conn.execute(
        "SELECT conversation_id FROM telegram_dm_reply_sessions WHERE chat_id='7441755677'"
    ).fetchone()[0] == "t_opaque-conversation_abc-123"


def test_telegram_ack_failure_does_not_downgrade_meta_confirmed_dm(conn, config):
    channel = connected_channel(conn)
    now = datetime.now(timezone.utc).isoformat()
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'conversation-1','person-77','رد مؤكد','pending',?,?)""",
        (channel, now, now),
    ).lastrowid
    conn.commit()

    class BrokenAcknowledgementTelegram(FakeTelegram):
        def send_message(self, token, chat_id, text, reply_markup=None):
            raise RuntimeError("Telegram acknowledgement unavailable")

    client = BrokenAcknowledgementTelegram()
    graph = FakeGraph()
    try:
        process_control_update(
            conn, cfg(config), client, cb(400, f"bs:dm:send:{outbox}"),
            lambda platform: graph,
        )
    except RuntimeError as exc:
        assert "Telegram acknowledgement unavailable" in str(exc)
    else:
        raise AssertionError("the fake Telegram acknowledgement should fail")

    row = conn.execute(
        "SELECT status,meta_message_id FROM telegram_dm_outbox WHERE id=?", (outbox,)
    ).fetchone()
    assert row["status"] == "sent"
    assert row["meta_message_id"] == "meta-msg-1"
    event = conn.execute(
        "SELECT status,notified_at FROM telegram_operation_log WHERE event_type='رسالة خاصة يدوية'"
    ).fetchone()
    assert event["status"] == "success"
    assert event["notified_at"] is None


def test_facebook_login_dm_uses_page_id_to_identify_own_participant(conn, config):
    channel = connected_channel(conn, linked_page_id="page-1", auth_method="facebook_login")
    graph = FakeGraph()
    graph.conversations[0]["participants"]["data"] = [
        {"id": "page-1", "username": "our_page"},
        {"id": "person-77", "username": "customer"},
    ]
    graph.get_instagram_conversation_messages = lambda conversation_id, token: (
        {"id": conversation_id, "participants": graph.conversations[0]["participants"]},
        [{"id": "m-page", "from": {"id": "page-1"}, "message": "رد الحساب"},
         {"id": "m-peer", "from": {"id": "person-77", "username": "customer"},
          "message": "مرحبا"}],
    )
    client = FakeTelegram()

    process_control_update(
        conn, cfg(config), client, cb(401, f"bs:dm:view:{channel}:9001"),
        lambda platform: graph,
    )
    session = conn.execute(
        "SELECT recipient_id FROM telegram_dm_reply_sessions WHERE chat_id='7441755677'"
    ).fetchone()
    assert session["recipient_id"] == "person-77"
    assert "حسابك: " in client.messages[-1][1]
