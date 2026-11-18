def test_telegram_dm_conversation_refs_schema_and_channel_cascade(conn):
    columns = {
        row["name"] for row in conn.execute(
            "PRAGMA table_info(telegram_dm_conversation_refs)"
        )
    }
    assert {"id", "chat_id", "channel_id", "conversation_id", "created_at"} <= columns
    channel = conn.execute(
        "INSERT INTO channels(platform,account_name) VALUES('instagram','inbox')"
    ).lastrowid
    conn.execute(
        """INSERT INTO telegram_dm_conversation_refs
           (chat_id,channel_id,conversation_id,created_at)
           VALUES('7441755677',?,?,?)""",
        (channel, "t_opaque-id", "2026-11-18T00:00:00Z"),
    )
    conn.commit()
    conn.execute("DELETE FROM channels WHERE id=?", (channel,))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_dm_conversation_refs"
    ).fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
