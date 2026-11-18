from dataclasses import replace

from worker.telegram_control import process_control_update


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, token, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return {"message_id": len(self.messages)}

    def answer_callback_query(self, token, callback_query_id, text=None):
        return {}


def cb(update_id, data):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb-{update_id}", "data": data,
        "from": {"id": 7441755677},
        "message": {"message_id": 10, "chat": {"id": 7441755677, "type": "private"}},
    }}


def cfg(config):
    return replace(config, telegram_bot_token="test-token",
                   telegram_allowed_chat_ids="7441755677")


def test_main_menu_exposes_separate_operation_log(conn, config):
    client = FakeTelegram()
    update = {"update_id": 1, "message": {
        "text": "/start", "from": {"id": 7441755677},
        "chat": {"id": 7441755677, "type": "private"}}}
    assert process_control_update(conn, cfg(config), client, update)
    buttons = [b for row in client.messages[0][2]["inline_keyboard"] for b in row]
    assert any(b["text"] == "📋 سجل العمليات" and b["callback_data"] == "bs:operations"
               for b in buttons)


def test_operation_log_shows_recent_success_and_failure(conn, config):
    conn.executemany(
        """INSERT INTO telegram_operation_log
           (event_key,event_type,status,publication_id,channel_name,caption,detail,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        [
            ("success-1", "نشر", "success", 11, "قناة الاختبار", "وصف ناجح",
             "تم تأكيد النشر من المنصة", "2026-11-18T12:00:00+00:00"),
            ("failure-1", "نشر", "failed", 12, "قناة الاختبار", "وصف فاشل",
             "رفضت المنصة الطلب", "2026-11-18T13:00:00+00:00"),
        ],
    )
    conn.commit()
    client = FakeTelegram()
    assert process_control_update(conn, cfg(config), client, cb(2, "bs:operations"))
    body = client.messages[-1][1]
    assert "وصف فاشل" in body
    assert "رفضت المنصة الطلب" in body
    assert "وصف ناجح" in body
    assert "نجاح" in body and "فشل" in body


def test_operation_log_pagination_is_bounded(conn, config):
    for i in range(10):
        conn.execute(
            """INSERT INTO telegram_operation_log
               (event_key,event_type,status,publication_id,detail,created_at)
               VALUES(?,?,?,?,?,?)""",
            (f"event-{i}", "نشر", "info", i, f"تفصيل {i}",
             f"2026-11-18T{i:02d}:00:00+00:00"),
        )
    conn.commit()
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(3, "bs:operations"))
    keyboard = client.messages[-1][2]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "bs:operations:page:1"
    process_control_update(conn, cfg(config), client, cb(4, "bs:operations:page:1"))
    assert "صفحة 2/2" in client.messages[-1][1]


def test_invalid_operation_log_page_is_rejected(conn, config):
    client = FakeTelegram()
    process_control_update(conn, cfg(config), client, cb(5, "bs:operations:page:-1"))
    assert "رقم صفحة غير صالح" in client.messages[-1][1]
