def test_telegram_operation_notifications_schema_and_recipient_uniqueness(conn):
    columns = {
        row["name"] for row in conn.execute(
            "PRAGMA table_info(telegram_operation_notifications)"
        )
    }
    assert {"id", "operation_id", "chat_id", "last_attempt_at", "attempt_count", "notified_at"} <= columns
    operation_id = conn.execute(
        """INSERT INTO telegram_operation_log(event_key,event_type,status,created_at)
           VALUES('notify-test','publish','success','2026-11-18T00:00:00Z')"""
    ).lastrowid
    conn.execute(
        """INSERT INTO telegram_operation_notifications(operation_id,chat_id,notified_at)
           VALUES(?,?,?)""",
        (operation_id, "7441755677", "2026-11-18T00:00:01Z"),
    )
    conn.execute(
        """INSERT OR IGNORE INTO telegram_operation_notifications(operation_id,chat_id,notified_at)
           VALUES(?,?,?)""",
        (operation_id, "7441755677", "2026-11-18T00:00:02Z"),
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_operation_notifications WHERE operation_id=?",
        (operation_id,),
    ).fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
