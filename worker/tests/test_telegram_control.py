from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def cfg(config, allowed="7441755677"):
    return replace(config, telegram_bot_token="test-token",
                   telegram_allowed_chat_ids=allowed)


def callback(update_id=1, chat_id=7441755677, data="bs:menu", chat_type="private", sender_id=None):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb-{update_id}", "data": data,
        "from": {"id": chat_id if sender_id is None else sender_id},
        "message": {"message_id": 10, "chat": {"id": chat_id, "type": chat_type}},
    }}


def test_start_sends_menu_only_to_allowlisted_private_chat(conn, config):
    client = FakeTelegram()
    update = {"update_id": 1, "message": {
        "text": "/start", "from": {"id": 7441755677},
        "chat": {"id": 7441755677, "type": "private"}}}
    assert process_control_update(conn, cfg(config), client, update)
    assert client.messages[0][0] == "7441755677"
    assert client.messages[0][2]["inline_keyboard"]


def test_unauthorized_callback_cannot_read_or_change_anything(conn, config):
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client,
                                   callback(chat_id=999, data="bs:comment:1"))
    assert client.messages == []
    assert client.answered == [("cb-1", "غير مصرح")]


def test_group_callback_is_rejected_even_if_group_id_is_allowlisted(conn, config):
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config, allowed="-1001"), client,
                                   callback(chat_id=-1001, chat_type="group"))
    assert client.messages == []


def test_status_callback_returns_counts_without_exposing_credentials(conn, config):
    client = FakeTelegram()
    conn.execute("INSERT INTO worker_heartbeat(id,last_seen_at) VALUES (1,'2026-11-18T00:00:00Z')")
    conn.commit()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:status"))
    text = client.messages[0][1]
    assert "آخر نبضة Worker" in text
    assert "قنوات Instagram" in text
    assert "test-token" not in text


def test_comment_rule_toggle_changes_only_selected_active_instagram_rule(conn, config):
    cur = conn.execute(
        """INSERT INTO channels(platform, account_name, remote_account_id, access_token, is_active)
           VALUES ('instagram','test','ig-1','secret',1)"""
    )
    channel_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO instagram_comment_rules(channel_id,media_id,keywords_json,enabled)
           VALUES (?, 'media-1', '[]', 0)""", (channel_id,)
    )
    rule_id = cur.lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client,
                                   callback(data=f"bs:comment:{rule_id}"))
    assert conn.execute("SELECT enabled FROM instagram_comment_rules WHERE id=?",
                        (rule_id,)).fetchone()[0] == 1
    assert "تم تشغيل" in client.messages[0][1]


def test_start_without_allowlist_preserves_chat_id_discovery(conn, config):
    client = FakeTelegram()
    update = {"update_id": 1, "message": {
        "text": "/start", "from": {"id": 42},
        "chat": {"id": 42, "type": "private"}}}
    assert process_control_update(conn, cfg(config, allowed=""), client, update) is False
    assert client.messages == []


def test_callback_rejects_allowlisted_chat_when_sender_is_not_that_user(conn, config):
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(chat_id=7441755677, sender_id=999, data="bs:comment:1"),
    )
    assert client.messages == []
    assert client.answered == [("cb-1", "غير مصرح")]


def test_posts_callback_joins_caption_from_posts_table(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name)
           VALUES ('instagram', 'test')"""
    ).lastrowid
    post = conn.execute(
        """INSERT INTO posts(caption, post_type, status)
           VALUES ('وصف تجريبي للمنشور', 'reel', 'scheduled')"""
    ).lastrowid
    conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2026-11-18T12:00:00Z', 'scheduled')""",
        (post, channel),
    )
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:posts"))
    assert "وصف تجريبي للمنشور" in client.messages[0][1]


def test_replayed_comment_toggle_update_is_idempotent(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, remote_account_id, access_token)
           VALUES ('instagram', 'test', 'ig-test', 'test-token')"""
    ).lastrowid
    rule_id = conn.execute(
        """INSERT INTO instagram_comment_rules(channel_id, media_id, enabled)
           VALUES (?, 'media-1', 0)""",
        (channel,),
    ).lastrowid
    conn.commit()
    update = callback(update_id=77, data=f"bs:comment:{rule_id}")
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, update)
    assert process_control_update(conn, cfg(config), client, update)
    assert conn.execute(
        "SELECT enabled FROM instagram_comment_rules WHERE id=?", (rule_id,)
    ).fetchone()[0] == 1



def test_main_menu_exposes_publish_now_action(conn, config):
    client = FakeTelegram()
    update = {"update_id": 901, "message": {
        "text": "/start", "from": {"id": 7441755677},
        "chat": {"id": 7441755677, "type": "private"}}}
    assert process_control_update(conn, cfg(config), client, update)
    keyboard = client.messages[0][2]["inline_keyboard"]
    assert any(button["callback_data"] == "bs:publish"
               for row in keyboard for button in row)


def test_publish_now_lists_only_scheduled_posts_on_connected_instagram(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, remote_account_id, access_token, is_active)
           VALUES ('instagram', 'connected', 'ig-1', 'secret', 1)"""
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type, status) VALUES ('Ready caption', 'reel', 'scheduled')"
    ).lastrowid
    conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00Z', 'scheduled')""",
        (post, channel),
    )
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:publish"))
    assert "Ready caption" in client.messages[0][1]
    buttons = client.messages[0][2]["inline_keyboard"]
    assert buttons[0][0]["callback_data"].startswith("bs:publish:")


def test_publish_now_moves_only_selected_scheduled_publication_to_due_queue(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, remote_account_id, access_token, is_active)
           VALUES ('instagram', 'connected', 'ig-1', 'secret', 1)"""
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type, status) VALUES ('Ready caption', 'reel', 'scheduled')"
    ).lastrowid
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00Z', 'scheduled')""",
        (post, channel),
    ).lastrowid
    conn.commit()
    update = callback(update_id=902, data=f"bs:publish:{publication}")
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, update)
    row = conn.execute(
        "SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()
    assert row["status"] == "scheduled"
    assert row["scheduled_at"] < "2099-01-01T12:00:00Z"
    assert "لم نؤكد النشر بعد" in client.messages[0][1]
    assert process_control_update(conn, cfg(config), client, update)
    assert conn.execute(
        "SELECT scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()[0] == row["scheduled_at"]


def test_publish_now_rejects_unconnected_or_non_scheduled_publication(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, remote_account_id, access_token, is_active)
           VALUES ('instagram', 'draft', NULL, NULL, 1)"""
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type, status) VALUES ('Not connected', 'reel', 'scheduled')"
    ).lastrowid
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00Z', 'pending_approval')""",
        (post, channel),
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client, callback(update_id=903, data=f"bs:publish:{publication}")
    )
    row = conn.execute(
        "SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()
    assert row["status"] == "pending_approval"
    assert row["scheduled_at"] == "2099-01-01T12:00:00Z"
    assert "لم يتم تقديم المنشور" in client.messages[0][1]



def test_publish_now_clears_retry_backoff(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, remote_account_id, access_token, is_active)
           VALUES ('instagram', 'connected', 'ig-1', 'secret', 1)"""
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type, status) VALUES ('Retry caption', 'reel', 'scheduled')"
    ).lastrowid
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, next_retry_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00Z', '2099-01-02T12:00:00Z', 'scheduled')""",
        (post, channel),
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=904, data=f"bs:publish:{publication}"),
    )
    row = conn.execute(
        "SELECT scheduled_at, next_retry_at, status FROM publications WHERE id=?",
        (publication,),
    ).fetchone()
    assert row["status"] == "scheduled"
    assert row["scheduled_at"] < "2099-01-01T12:00:00Z"
    assert row["next_retry_at"] is None


def _scheduled_post(conn, *, status="scheduled", timezone_name="Asia/Aden"):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, timezone)
           VALUES ('instagram', 'قناة الجدولة', ?)""",
        (timezone_name,),
    ).lastrowid
    post = conn.execute(
        """INSERT INTO posts(caption, post_type, status)
           VALUES ('وصف المنشور المراد إدارته', 'reel', 'scheduled')"""
    ).lastrowid
    asset = conn.execute(
        """INSERT INTO assets(content_hash, media_kind, original_filename, storage_path)
           VALUES ('schedule-hash-' || ?, 'video', 'clip.mp4', 'assets/clip.mp4')""",
        (str(post),),
    ).lastrowid
    conn.execute(
        "INSERT INTO post_assets(post_id, asset_id, sort_order) VALUES (?, ?, 0)",
        (post, asset),
    )
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00+00:00', ?)""",
        (post, channel, status),
    ).lastrowid
    conn.commit()
    return post, asset, publication


def message_update(update_id, text, chat_id=7441755677, sender_id=None, chat_type="private"):
    return {"update_id": update_id, "message": {
        "text": text,
        "from": {"id": chat_id if sender_id is None else sender_id},
        "chat": {"id": chat_id, "type": chat_type},
    }}


def test_main_menu_has_separate_schedule_management_button(conn, config):
    client = FakeTelegram()
    update = message_update(1100, "/start")
    assert process_control_update(conn, cfg(config), client, update)
    buttons = [button for row in client.messages[0][2]["inline_keyboard"] for button in row]
    assert any(button["callback_data"] == "bs:schedule" for button in buttons)
    assert any(button["callback_data"] == "bs:publish" for button in buttons)


def test_schedule_list_shows_scheduled_items_and_detail_buttons(conn, config):
    _, _, first = _scheduled_post(conn)
    _, _, second = _scheduled_post(conn, status="pending_approval")
    _, _, _ = _scheduled_post(conn, status="failed")
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(update_id=1101, data="bs:schedule"))
    body, keyboard = client.messages[0][1], client.messages[0][2]["inline_keyboard"]
    assert f"#{first}" in body and f"#{second}" in body
    assert "failed" not in body
    callbacks = [button["callback_data"] for row in keyboard for button in row]
    assert f"bs:schedule:view:{first}" in callbacks
    assert f"bs:schedule:view:{second}" in callbacks


def test_schedule_details_show_caption_channel_and_media_without_mutation(conn, config):
    post, asset, publication = _scheduled_post(conn)
    before = conn.execute("SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)).fetchone()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=1102, data=f"bs:schedule:view:{publication}"),
    )
    body, keyboard = client.messages[0][1], client.messages[0][2]["inline_keyboard"]
    assert "وصف المنشور المراد إدارته" in body
    assert "قناة الجدولة" in body
    assert "clip.mp4" in body
    assert any(button["callback_data"] == f"bs:schedule:reschedule:{publication}"
               for row in keyboard for button in row)
    assert any(button["callback_data"] == f"bs:schedule:cancel:{publication}"
               for row in keyboard for button in row)
    after = conn.execute("SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)).fetchone()
    assert tuple(after) == tuple(before)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE id=?", (asset,)).fetchone()[0] == 1


def test_reschedule_callback_creates_persistent_session(conn, config):
    _, _, publication = _scheduled_post(conn)
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=1103, data=f"bs:schedule:reschedule:{publication}"),
    )
    session = conn.execute(
        "SELECT publication_id, timezone FROM telegram_schedule_sessions WHERE chat_id='7441755677'"
    ).fetchone()
    assert session["publication_id"] == publication
    assert session["timezone"] == "Asia/Aden"
    assert "YYYY-MM-DD HH:MM" in client.messages[0][1]


def test_reschedule_message_updates_only_schedule_and_preserves_media(conn, config):
    post, asset, publication = _scheduled_post(conn)
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=1104, data=f"bs:schedule:reschedule:{publication}"),
    )
    new_local = (datetime.now(timezone.utc) + timedelta(days=3)).astimezone(
        ZoneInfo("Asia/Aden")
    ).replace(second=0, microsecond=0)
    entered = new_local.strftime("%Y-%m-%d %H:%M")
    assert process_control_update(
        conn, cfg(config), client, message_update(1105, entered)
    )
    row = conn.execute(
        "SELECT status, scheduled_at, next_retry_at FROM publications WHERE id=?",
        (publication,),
    ).fetchone()
    assert row["status"] == "scheduled"
    assert row["next_retry_at"] is None
    actual = datetime.fromisoformat(row["scheduled_at"]).astimezone(timezone.utc)
    expected = new_local.astimezone(timezone.utc)
    assert actual == expected
    assert conn.execute("SELECT status FROM posts WHERE id=?", (post,)).fetchone()[0] == "scheduled"
    assert conn.execute("SELECT COUNT(*) FROM post_assets WHERE post_id=?", (post,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE id=?", (asset,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM telegram_schedule_sessions").fetchone()[0] == 0
    assert "تم تأجيل المنشور" in client.messages[-1][1]


def test_invalid_past_reschedule_keeps_session_and_does_not_change_publication(conn, config):
    _, _, publication = _scheduled_post(conn)
    client = FakeTelegram()
    process_control_update(
        conn, cfg(config), client,
        callback(update_id=1106, data=f"bs:schedule:reschedule:{publication}"),
    )
    original = conn.execute(
        "SELECT scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()[0]
    process_control_update(conn, cfg(config), client, message_update(1107, "2020-01-01 10:00"))
    assert conn.execute(
        "SELECT scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()[0] == original
    assert conn.execute("SELECT COUNT(*) FROM telegram_schedule_sessions").fetchone()[0] == 1
    assert "في المستقبل" in client.messages[-1][1]


def test_cancel_schedule_marks_post_draft_but_preserves_post_and_media(conn, config):
    post, asset, publication = _scheduled_post(conn)
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=1108, data=f"bs:schedule:cancel:{publication}"),
    )
    assert conn.execute(
        "SELECT status FROM publications WHERE id=?", (publication,)
    ).fetchone()[0] == "canceled"
    assert conn.execute("SELECT status FROM posts WHERE id=?", (post,)).fetchone()[0] == "draft"
    assert conn.execute("SELECT COUNT(*) FROM post_assets WHERE post_id=?", (post,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE id=?", (asset,)).fetchone()[0] == 1
    assert "لم نحذف المنشور أو أي ملف وسائط" in client.messages[-1][1]


def test_cancel_one_publication_does_not_draft_post_with_another_active_schedule(conn, config):
    post, _, publication = _scheduled_post(conn)
    other_channel = conn.execute(
        "INSERT INTO channels(platform, account_name) VALUES ('instagram', 'قناة ثانية')"
    ).lastrowid
    other = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-02T12:00:00+00:00', 'scheduled')""",
        (post, other_channel),
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    process_control_update(
        conn, cfg(config), client,
        callback(update_id=1109, data=f"bs:schedule:cancel:{publication}"),
    )
    assert conn.execute("SELECT status FROM publications WHERE id=?", (publication,)).fetchone()[0] == "canceled"
    assert conn.execute("SELECT status FROM publications WHERE id=?", (other,)).fetchone()[0] == "scheduled"
    assert conn.execute("SELECT status FROM posts WHERE id=?", (post,)).fetchone()[0] == "scheduled"


def test_schedule_controls_reject_publication_already_publishing(conn, config):
    _, _, publication = _scheduled_post(conn, status="publishing")
    client = FakeTelegram()
    process_control_update(
        conn, cfg(config), client,
        callback(update_id=1110, data=f"bs:schedule:cancel:{publication}"),
    )
    assert conn.execute("SELECT status FROM publications WHERE id=?", (publication,)).fetchone()[0] == "publishing"
    assert "لم يتم الإلغاء" in client.messages[-1][1]


def test_unauthorized_text_cannot_consume_another_chats_schedule_session(conn, config):
    _, _, publication = _scheduled_post(conn)
    conn.execute(
        """INSERT INTO telegram_schedule_sessions(chat_id, publication_id, timezone, created_at)
           VALUES ('7441755677', ?, 'Asia/Aden', ?)""",
        (publication, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        message_update(1111, "2099-02-01 10:00", chat_id=999, sender_id=999),
    ) is False
    assert conn.execute("SELECT COUNT(*) FROM telegram_schedule_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT scheduled_at FROM publications WHERE id=?", (publication,)).fetchone()[0] == "2099-01-01T12:00:00+00:00"
