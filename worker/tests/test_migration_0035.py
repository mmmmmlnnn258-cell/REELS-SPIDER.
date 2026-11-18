def test_telegram_operation_log_schema_and_event_key_idempotency(conn):
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(telegram_operation_log)")
    }
    assert {
        "event_key", "event_type", "status", "publication_id", "channel_name",
        "caption", "detail", "created_at", "notified_at",
    } <= columns
    conn.execute(
        """INSERT INTO telegram_operation_log(event_key,event_type,status,created_at)
           VALUES('unique-event','publish','success','2026-11-18T00:00:00Z')"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO telegram_operation_log(event_key,event_type,status,created_at)
           VALUES('unique-event','publish','failed','2026-11-18T00:01:00Z')"""
    )
    conn.commit()
    rows = conn.execute(
        "SELECT status FROM telegram_operation_log WHERE event_key='unique-event'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
