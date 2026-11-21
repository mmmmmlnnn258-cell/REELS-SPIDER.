from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from worker.telegram_control import (
    process_control_update, _zernio_dm_window,
    _connected_ig_channels, _telegram_can_access_comment_rule,
)


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
    assert any(value.startswith(f"bs:schedule:view:{first}:") for value in callbacks)
    assert any(value.startswith(f"bs:schedule:view:{second}:") for value in callbacks)


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


def test_schedule_list_paginates_more_than_eight_and_details_return_to_page(conn, config):
    publications = [_scheduled_post(conn)[2] for _ in range(9)]
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, callback(update_id=1112, data="bs:schedule"))
    first_callbacks = [b["callback_data"] for row in client.messages[-1][2]["inline_keyboard"] for b in row]
    assert "bs:schedule:page:1" in first_callbacks
    assert sum(value.startswith("bs:schedule:view:") for value in first_callbacks) == 8
    process_control_update(conn, cfg(config), client, callback(update_id=1113, data="bs:schedule:page:1"))
    second_callbacks = [b["callback_data"] for row in client.messages[-1][2]["inline_keyboard"] for b in row]
    assert f"bs:schedule:view:{publications[-1]}:1" in second_callbacks
    process_control_update(
        conn, cfg(config), client,
        callback(update_id=1114, data=f"bs:schedule:view:{publications[-1]}:1"),
    )
    back = [b["callback_data"] for row in client.messages[-1][2]["inline_keyboard"] for b in row]
    assert "bs:schedule:page:1" in back


def test_schedule_menu_button_cancels_pending_date_entry(conn, config):
    _, _, publication = _scheduled_post(conn)
    conn.execute(
        """INSERT INTO telegram_schedule_sessions(chat_id, publication_id, timezone, created_at)
           VALUES ('7441755677', ?, 'Asia/Aden', ?)""",
        (publication, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    client = FakeTelegram()
    process_control_update(
        conn, cfg(config), client,
        callback(update_id=1115, data="bs:schedule"),
    )
    assert conn.execute("SELECT COUNT(*) FROM telegram_schedule_sessions").fetchone()[0] == 0



def test_main_menu_exposes_separate_global_publishing_control(conn, config):
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:menu"))
    buttons = [button for row in client.messages[0][2]["inline_keyboard"] for button in row]
    assert any(button["callback_data"] == "bs:publishing_control" for button in buttons)


def test_global_publishing_control_shows_pause_action_when_active(conn, config):
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client, callback(update_id=1200, data="bs:publishing_control")
    )
    body, keyboard = client.messages[0][1], client.messages[0][2]["inline_keyboard"]
    assert "الحالة العامة: نشط" in body
    assert any(button["callback_data"] == "bs:publishing:pause"
               for row in keyboard for button in row)


def test_global_pause_and_resume_are_persisted_and_idempotent(conn, config):
    client = FakeTelegram()
    pause = callback(update_id=1201, data="bs:publishing:pause")
    assert process_control_update(conn, cfg(config), client, pause)
    assert conn.execute(
        "SELECT is_paused, updated_by_chat_id FROM publishing_control WHERE id=1"
    ).fetchone()["is_paused"] == 1
    first_message_count = len(client.messages)
    assert process_control_update(conn, cfg(config), client, pause)
    assert len(client.messages) == first_message_count
    assert "تم إيقاف" in client.messages[0][1]

    assert process_control_update(
        conn, cfg(config), client, callback(update_id=1202, data="bs:publishing:resume")
    )
    assert conn.execute(
        "SELECT is_paused FROM publishing_control WHERE id=1"
    ).fetchone()[0] == 0
    assert "تم استئناف" in client.messages[-1][1]


def test_global_publishing_controls_reject_unauthorized_user(conn, config):
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=1203, chat_id=7441755677, sender_id=999,
                 data="bs:publishing:pause"),
    )
    assert conn.execute(
        "SELECT is_paused FROM publishing_control WHERE id=1"
    ).fetchone()[0] == 0
    assert client.messages == []


def test_accounts_screen_lists_only_the_callers_independent_instagram_accounts(conn, config):
    conn.execute(
        """INSERT INTO instagram_accounts
           (telegram_chat_id, telegram_user_id, instagram_user_id, username,
            access_token, token_expires_at, auth_method, status,
            created_at, updated_at, last_verified_at)
           VALUES ('7441755677', '7441755677', 'ig-42', 'my_brand',
                   'encrypted-secret', '2099-01-01T00:00:00Z',
                   'instagram_login', 'connected', '2026-11-20', '2026-11-20', '2026-11-20')"""
    )
    conn.execute(
        """INSERT INTO instagram_accounts
           (telegram_chat_id, telegram_user_id, instagram_user_id, username,
            access_token, auth_method, status, created_at, updated_at, last_verified_at)
           VALUES ('other-chat', 'other-user', 'ig-other', 'someone_else',
                   'encrypted-secret', 'instagram_login', 'connected',
                   '2026-11-20', '2026-11-20', '2026-11-20')"""
    )
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:accounts"))
    body = client.messages[0][1]
    assert "@my_brand" in body
    assert "someone_else" not in body
    assert "encrypted-secret" not in body
    buttons = [button for row in client.messages[0][2]["inline_keyboard"] for button in row]
    assert any(button["callback_data"] == "bs:account:add" for button in buttons)


def test_add_account_callback_creates_channel_free_instagram_ticket(conn, config):
    client = FakeTelegram()
    configured = replace(cfg(config), telegram_dashboard_url="https://dashboard.example.test")
    assert process_control_update(
        conn, configured, client, callback(data="bs:account:add"),
    )
    row = conn.execute(
        "SELECT telegram_chat_id, telegram_user_id, platform, status FROM telegram_account_link_tickets"
    ).fetchone()
    assert tuple(row) == ("7441755677", "7441755677", "instagram", "pending")
    assert "channel_id" not in row.keys()
    keyboard = client.messages[0][2]["inline_keyboard"]
    assert keyboard[0][0]["url"].startswith(
        "https://dashboard.example.test/api/channels/instagram/zernio/telegram?ticket="
    )


def test_selected_instagram_account_is_scoped_to_telegram_identity(conn, config):
    account_id = conn.execute(
        """INSERT INTO instagram_accounts
           (telegram_chat_id, telegram_user_id, instagram_user_id, username,
            access_token, auth_method, status, created_at, updated_at, last_verified_at)
           VALUES ('7441755677', '7441755677', 'ig-selected', 'selected_brand',
                   'encrypted-token', 'instagram_login', 'connected',
                   '2026-11-20', '2026-11-20', '2026-11-20')"""
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(data=f"bs:account:select:{account_id}"),
    )
    selected = conn.execute(
        """SELECT selected_instagram_account_id FROM telegram_account_preferences
            WHERE telegram_chat_id='7441755677' AND telegram_user_id='7441755677'"""
    ).fetchone()
    assert selected["selected_instagram_account_id"] == account_id
    assert "@selected_brand" in client.messages[0][1]


def test_publish_now_failure_notice_is_sent_after_database_transaction(conn, config):
    channel = conn.execute(
        """INSERT INTO channels(platform, account_name, is_active)
           VALUES ('instagram', 'not-connected', 1)"""
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type, status) VALUES ('Draft', 'reel', 'scheduled')"
    ).lastrowid
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00Z', 'scheduled')""",
        (post, channel),
    ).lastrowid
    conn.commit()

    class TransactionCheckingTelegram(FakeTelegram):
        def send_message(self, token, chat_id, text, reply_markup=None):
            assert not conn.in_transaction, "Telegram network call must happen outside SQLite transaction"
            return super().send_message(token, chat_id, text, reply_markup)

    client = TransactionCheckingTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=905, data=f"bs:publish:{publication}"),
    )
    assert "لم يتم تقديم المنشور" in client.messages[0][1]
    assert conn.execute(
        "SELECT status FROM publications WHERE id=?", (publication,)
    ).fetchone()[0] == "scheduled"


def test_accounts_screen_exposes_add_instagram_action(conn, config):
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client, callback(data="bs:accounts")
    )
    body, keyboard = client.messages[0][1], client.messages[0][2]["inline_keyboard"]
    assert "لا توجد حسابات مرتبطة حتى الآن" in body
    assert any(
        button["callback_data"] == "bs:account:add"
        for row in keyboard for button in row
    )


def test_zernio_dm_window_requires_recent_inbound_message():
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    allowed, error = _zernio_dm_window([
        {"direction": "incoming", "createdAt": "2026-11-21T11:30:00Z"}
    ], now)
    assert allowed is True
    assert error == ""


def test_zernio_dm_window_fails_closed_when_last_inbound_is_old_or_missing():
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    allowed, error = _zernio_dm_window([
        {"direction": "incoming", "createdAt": "2026-11-20T10:00:00Z"},
        {"direction": "outgoing", "createdAt": "2026-11-21T11:59:00Z"},
    ], now)
    assert allowed is False
    assert "24 ساعة" in error
    allowed, error = _zernio_dm_window([
        {"direction": "outgoing", "createdAt": "2026-11-21T11:59:00Z"}
    ], now)
    assert allowed is False
    assert "لم تصل رسالة واردة" in error


def test_telegram_dm_list_reads_zernio_conversations(monkeypatch, conn, config):
    channel_id = conn.execute(
        """INSERT INTO channels
           (platform,account_name,zernio_account_id,zernio_profile_id,
            zernio_telegram_chat_id,zernio_telegram_user_id,is_active)
           VALUES ('instagram','zernio channel','z-account','z-profile',
                   '7441755677','7441755677',1)"""
    ).lastrowid
    conn.commit()
    class FakeZernio:
        def __init__(self):
            pass
        def list_inbox_conversations(self, *, account_id, limit):
            assert account_id == "z-account"
            return [{
                "id": "thread-z-1", "participantName": "Customer",
                "participantId": "customer-1", "updatedTime": "2026-11-21T11:00:00Z",
                "unreadCount": 2,
            }]
    monkeypatch.setattr("worker.zernio_client.ZernioClient", FakeZernio)
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:dms"), graph_client_for=None)
    assert "محادثة عبر Zernio" in client.messages[0][1]
    ref = conn.execute(
        "SELECT conversation_id FROM telegram_dm_conversation_refs WHERE channel_id=?",
        (channel_id,),
    ).fetchone()
    assert ref["conversation_id"] == "thread-z-1"


def test_telegram_comment_inbox_reads_zernio_comments(monkeypatch, conn, config):
    channel_id = conn.execute(
        """INSERT INTO channels
           (platform,account_name,zernio_account_id,zernio_profile_id,
            zernio_telegram_chat_id,zernio_telegram_user_id,is_active)
           VALUES ('instagram','zernio channel','z-account','z-profile',
                   '7441755677','7441755677',1)"""
    ).lastrowid
    rule_id = conn.execute(
        """INSERT INTO instagram_comment_rules(channel_id,media_id,keywords_json,private_reply_text,enabled)
           VALUES (?, 'ig-media-1', '[\"hello\"]', 'Welcome', 1)""", (channel_id,)
    ).lastrowid
    conn.commit()
    class FakeZernio:
        def __init__(self):
            pass
        def list_post_comments(self, post_id, *, account_id, limit):
            assert post_id == "ig-media-1"
            assert account_id == "z-account"
            return [{"id": "comment-1", "text": "hello", "author": {"username": "customer"}}]
    monkeypatch.setattr("worker.zernio_client.ZernioClient", FakeZernio)
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client, callback(data=f"bs:comment:fetch:{rule_id}"),
        graph_client_for=None,
    )
    assert "customer: hello" in client.messages[0][1]


def test_zernio_comment_rule_can_be_toggled_from_telegram(conn, config):
    channel_id = conn.execute(
        """INSERT INTO channels
           (platform,account_name,zernio_account_id,zernio_profile_id,
            zernio_telegram_chat_id,zernio_telegram_user_id,is_active)
           VALUES ('instagram','zernio channel','z-account','z-profile',
                   '7441755677','7441755677',1)"""
    ).lastrowid
    rule_id = conn.execute(
        """INSERT INTO instagram_comment_rules(channel_id,media_id,keywords_json,private_reply_text,enabled)
           VALUES (?, 'ig-media-1', '[\"hello\"]', 'Welcome', 0)""", (channel_id,)
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client, callback(data=f"bs:comment:{rule_id}"),
        graph_client_for=None,
    )
    assert conn.execute(
        "SELECT enabled FROM instagram_comment_rules WHERE id=?", (rule_id,)
    ).fetchone()[0] == 1
    assert "تم حفظ حالة القاعدة محلياً" in client.messages[0][1]


def test_zernio_account_appears_in_telegram_account_hub(conn, config):
    channel = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_username, zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'spider_test', 'zernio-ig-1', 'profile-1',
                   'spider_test', '7441755677', '7441755677', 1)"""
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:accounts"))
    assert "@spider_test" in client.messages[0][1]
    assert "zernio-ig-1" in client.messages[0][1]
    buttons = client.messages[0][2]["inline_keyboard"]
    assert any(
        button["callback_data"] == f"bs:account:select:z:{channel}"
        for row in buttons for button in row
    )


def test_zernio_account_can_be_selected_and_is_scoped_to_telegram_owner(conn, config):
    channel = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_username, zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'spider_test', 'zernio-ig-2', 'profile-1',
                   'spider_test', '7441755677', '7441755677', 1)"""
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client, callback(data=f"bs:account:select:z:{channel}")
    )
    selected = conn.execute(
        """SELECT selected_instagram_account_id, selected_zernio_channel_id
             FROM telegram_account_preferences
            WHERE telegram_chat_id='7441755677' AND telegram_user_id='7441755677'"""
    ).fetchone()
    assert selected["selected_instagram_account_id"] is None
    assert selected["selected_zernio_channel_id"] == channel
    assert "تم اختيار حساب Instagram @spider_test" in client.messages[0][1]

    # A different Telegram identity cannot select another user's Zernio account.
    other = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), other,
        callback(update_id=902, data=f"bs:account:select:z:{channel}", sender_id=999),
    )
    assert other.messages == []
    assert other.answered == [("cb-902", "غير مصرح")]


def test_selected_zernio_account_scopes_telegram_inbox_channels(conn):
    first = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'first', 'z-1', 'p-1', '7441755677', '7441755677', 1)"""
    ).lastrowid
    conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'second', 'z-2', 'p-1', '7441755677', '7441755677', 1)"""
    )
    conn.execute(
        """INSERT INTO telegram_account_preferences
           (telegram_chat_id, telegram_user_id, selected_zernio_channel_id, updated_at)
           VALUES ('7441755677', '7441755677', ?, '2026-11-21T00:00:00Z')""",
        (first,),
    )
    conn.commit()
    channels = _connected_ig_channels(conn, "7441755677", "7441755677")
    assert [int(row["id"]) for row in channels] == [first]


def test_zernio_dm_channel_access_requires_owner_and_selected_account(conn):
    owned = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'owned', 'z-owned-dm', 'p-1',
                   '7441755677', '7441755677', 1)"""
    ).lastrowid
    other = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'other', 'z-other-dm', 'p-1',
                   '999', '999', 1)"""
    ).lastrowid
    conn.commit()
    from worker.telegram_control import _telegram_can_access_zernio_channel
    assert _telegram_can_access_zernio_channel(conn, owned, "7441755677", "7441755677")
    assert not _telegram_can_access_zernio_channel(conn, owned, "7441755677", "999")
    assert not _telegram_can_access_zernio_channel(conn, other, "7441755677", "7441755677")

    conn.execute(
        """INSERT INTO telegram_account_preferences
           (telegram_chat_id, telegram_user_id, selected_zernio_channel_id, updated_at)
           VALUES ('7441755677', '7441755677', ?, '2026-11-21T00:00:00Z')""",
        (other,),
    )
    conn.commit()
    assert not _telegram_can_access_zernio_channel(conn, owned, "7441755677", "7441755677")


def test_zernio_dm_legacy_numeric_callback_cannot_open_another_users_account(conn, config):
    channel_id = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'other', 'z-other-dm', 'p-1', '999', '999', 1)"""
    ).lastrowid
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(
        conn, cfg(config), client,
        callback(data=f"bs:dm:view:{channel_id}:123456789"), graph_client_for=None,
    )
    assert "لا تملك صلاحية فتح هذه المحادثة" in client.messages[0][1]


def test_comment_rule_access_requires_owned_selected_zernio_account(conn):
    channel = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'owned', 'z-owned', 'p-1',
                   '7441755677', '7441755677', 1)"""
    ).lastrowid
    rule = conn.execute(
        """INSERT INTO instagram_comment_rules(channel_id, media_id, keywords_json, enabled)
           VALUES (?, 'media-owned', '[]', 1)""",
        (channel,),
    ).lastrowid
    conn.commit()
    assert _telegram_can_access_comment_rule(conn, rule, "7441755677", "7441755677")
    assert not _telegram_can_access_comment_rule(conn, rule, "7441755677", "999")
    other_channel = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'other-owner', 'z-other', 'p-1',
                   '999', '999', 1)"""
    ).lastrowid
    conn.execute(
        """INSERT INTO telegram_account_preferences
           (telegram_chat_id, telegram_user_id, selected_zernio_channel_id, updated_at)
           VALUES ('7441755677', '7441755677', ?, '2026-11-21T00:00:00Z')""",
        (other_channel,),
    )
    conn.commit()
    assert not _telegram_can_access_comment_rule(conn, rule, "7441755677", "7441755677")


def test_publish_now_lists_and_advances_owned_zernio_publication(conn, config):
    channel = conn.execute(
        """INSERT INTO channels
           (platform, account_name, zernio_account_id, zernio_profile_id,
            zernio_telegram_chat_id, zernio_telegram_user_id, is_active)
           VALUES ('instagram', 'z_publish', 'z-publish-1', 'p-1',
                   '7441755677', '7441755677', 1)"""
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type, status) VALUES ('Zernio caption', 'reel', 'scheduled')"
    ).lastrowid
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at, status)
           VALUES (?, ?, '2099-01-01T12:00:00Z', 'scheduled')""",
        (post, channel),
    ).lastrowid
    conn.execute(
        """INSERT INTO telegram_account_preferences
           (telegram_chat_id, telegram_user_id, selected_zernio_channel_id, updated_at)
           VALUES ('7441755677', '7441755677', ?, '2026-11-21T00:00:00Z')""",
        (channel,),
    )
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, callback(data="bs:publish"))
    assert "Zernio caption" in client.messages[0][1]
    assert process_control_update(
        conn, cfg(config), client,
        callback(update_id=903, data=f"bs:publish:{publication}"),
    )
    row = conn.execute(
        "SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()
    assert row["status"] == "scheduled"
    assert row["scheduled_at"] < "2099-01-01T12:00:00Z"
