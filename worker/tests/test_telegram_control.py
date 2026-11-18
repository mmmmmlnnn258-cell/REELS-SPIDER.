from dataclasses import replace

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
    assert conn.execute(
        "SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone() == conn.execute(
        "SELECT status, scheduled_at FROM publications WHERE id=?", (publication,)
    ).fetchone()
    assert "لم يتم تقديم المنشور" in client.messages[0][1]
