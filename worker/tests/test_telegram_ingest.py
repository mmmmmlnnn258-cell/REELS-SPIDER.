from __future__ import annotations

from dataclasses import replace

import pytest

from worker.telegram_ingest import process_update


def mp4_box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def make_mp4() -> bytes:
    ftyp = mp4_box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isom")
    mvhd = (
        b"\x00\x00\x00\x00" + b"\x00" * 8
        + (1000).to_bytes(4, "big") + (12000).to_bytes(4, "big")
        + b"\x00" * 80
    )
    mvhd_box = mp4_box(b"mvhd", mvhd)
    matrix = (
        (65536).to_bytes(4, "big", signed=True) + b"\x00" * 12
        + (65536).to_bytes(4, "big", signed=True) + b"\x00" * 12
        + (0x40000000).to_bytes(4, "big")
    )
    tkhd = (
        b"\x00\x00\x00\x07" + b"\x00" * 8
        + (1).to_bytes(4, "big") + b"\x00" * 4 + (12000).to_bytes(4, "big")
        + b"\x00" * 8 + b"\x00" * 8 + matrix
        + (720 * 65536).to_bytes(4, "big") + (1280 * 65536).to_bytes(4, "big")
    )
    hdlr = b"\x00" * 8 + b"vide" + b"\x00" * 12 + b"Video\x00"
    trak = mp4_box(b"trak", mp4_box(b"tkhd", tkhd) + mp4_box(b"mdia", mp4_box(b"hdlr", hdlr)))
    moov = mp4_box(b"moov", mvhd_box + trak)
    return ftyp + moov + mp4_box(b"mdat", b"video-payload")


VIDEO_BYTES = make_mp4()


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.file_ids = []
    def get_file(self, token, file_id):
        self.file_ids.append(file_id)
        return {"file_path": "videos/reel.mp4"}
    def download_file(self, token, file_path, max_bytes):
        assert file_path == "videos/reel.mp4"
        assert len(VIDEO_BYTES) <= max_bytes
        return VIDEO_BYTES
    def send_message(self, token, chat_id, text):
        self.messages.append((chat_id, text))
        return {"message_id": 1}


def add_channel(conn, *, active=1, token="ig-token", remote_id="1784001"):
    cur = conn.execute(
        """INSERT INTO channels
           (platform, account_name, timezone, remote_account_id, access_token, is_active)
           VALUES ('instagram', 'An Instagram account', 'UTC', ?, ?, ?)""",
        (remote_id, token, active),
    )
    conn.commit()
    return int(cur.lastrowid)


def configured(config, **kwargs):
    values = {
        "telegram_bot_token": "bot-secret",
        "telegram_allowed_chat_ids": "42",
        "telegram_default_instagram_channel_id": None,
        "telegram_max_file_bytes": 1024 * 1024,
    }
    values.update(kwargs)
    return replace(config, **values)


def video_update(update_id=100, chat_id=42):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 9,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id},
            "caption": "Test Reel caption",
            "video": {
                "file_id": "telegram-file-id",
                "file_size": len(VIDEO_BYTES),
                "mime_type": "video/mp4",
            },
        },
    }


def test_video_update_creates_reel_and_scheduled_publication_for_first_connected_active_ig(
    conn, config, tmp_path
):
    # An earlier active but unconnected test channel must never be selected.
    add_channel(conn, token=None, remote_id=None)
    expected_channel = add_channel(conn)
    add_channel(conn, remote_id="1784002")

    cfg = configured(config, asset_storage_dir=tmp_path / "assets")
    client = FakeTelegram()
    result = process_update(conn, cfg, client, video_update())

    assert result == "processed"
    post = conn.execute("SELECT * FROM posts").fetchone()
    assert post["post_type"] == "video"
    assert post["content_kind"] == "one_time"
    assert post["content_status"] == "ready"
    assert post["caption"] == "Test Reel caption"
    asset = conn.execute(
        """SELECT a.* FROM assets a
           JOIN post_assets pa ON pa.asset_id = a.id WHERE pa.post_id = ?""",
        (post["id"],),
    ).fetchone()
    assert asset["media_kind"] == "video"
    assert asset["mime_type"] == "video/mp4"
    assert asset["duration_ms"] == 12000
    assert asset["width"] == 720
    assert asset["height"] == 1280
    assert (cfg.asset_storage_dir / asset["storage_path"]).read_bytes() == VIDEO_BYTES
    publication = conn.execute("SELECT * FROM publications").fetchone()
    assert publication["channel_id"] == expected_channel
    assert publication["status"] == "scheduled"
    assert conn.execute("SELECT status FROM telegram_ingest_updates").fetchone()["status"] == "processed"
    assert client.messages and "تم استلام الفيديو" in client.messages[0][1]


def test_update_is_idempotent(conn, config, tmp_path):
    add_channel(conn)
    cfg = configured(config, asset_storage_dir=tmp_path / "assets")
    client = FakeTelegram()
    update = video_update()
    assert process_update(conn, cfg, client, update) == "processed"
    assert process_update(conn, cfg, client, update) == "ignored"
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1


def test_unauthorized_chat_is_ignored_without_downloading(conn, config, tmp_path):
    add_channel(conn)
    cfg = configured(config, asset_storage_dir=tmp_path / "assets")
    client = FakeTelegram()
    assert process_update(conn, cfg, client, video_update(chat_id=999)) == "ignored"
    assert client.file_ids == []
    assert conn.execute("SELECT status FROM telegram_ingest_updates").fetchone()["status"] == "ignored"


def test_group_video_is_ignored_even_if_group_chat_id_is_allowlisted(conn, config, tmp_path):
    add_channel(conn)
    cfg = configured(
        config,
        telegram_allowed_chat_ids="-10042",
        asset_storage_dir=tmp_path / "assets",
    )
    client = FakeTelegram()
    update = video_update(chat_id=-10042)
    update["message"]["chat"]["type"] = "supergroup"
    update["message"]["from"] = {"id": 42}
    assert process_update(conn, cfg, client, update) == "ignored"
    assert client.file_ids == []
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_private_video_requires_sender_id_to_match_chat_id(conn, config, tmp_path):
    add_channel(conn)
    cfg = configured(config, asset_storage_dir=tmp_path / "assets")
    client = FakeTelegram()
    update = video_update()
    update["message"]["from"] = {"id": 999}
    assert process_update(conn, cfg, client, update) == "ignored"
    assert client.file_ids == []
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_channel_post_is_never_ingested(conn, config, tmp_path):
    add_channel(conn)
    cfg = configured(config, telegram_allowed_chat_ids="-10042", asset_storage_dir=tmp_path / "assets")
    client = FakeTelegram()
    update = video_update(chat_id=-10042)
    update["channel_post"] = update.pop("message")
    assert process_update(conn, cfg, client, update) == "ignored"
    assert client.file_ids == []
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_configured_default_channel_is_used(conn, config, tmp_path):
    first = add_channel(conn)
    preferred = add_channel(conn, remote_id="1784999")
    cfg = configured(
        config,
        asset_storage_dir=tmp_path / "assets",
        telegram_default_instagram_channel_id=preferred,
    )
    assert process_update(conn, cfg, FakeTelegram(), video_update()) == "processed"
    publication = conn.execute("SELECT channel_id FROM publications").fetchone()
    assert publication["channel_id"] == preferred
    assert publication["channel_id"] != first


def test_no_connected_active_instagram_channel_fails_closed(conn, config, tmp_path):
    add_channel(conn, token=None, remote_id=None)
    cfg = configured(config, asset_storage_dir=tmp_path / "assets")
    client = FakeTelegram()
    assert process_update(conn, cfg, client, video_update()) == "failed"
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM telegram_ingest_updates").fetchone()["status"] == "failed"



def test_start_command_discloses_own_chat_id_only_in_discovery_mode(conn, config):
    cfg = configured(config, telegram_allowed_chat_ids="")
    client = FakeTelegram()
    update = {
        "update_id": 101,
        "message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "/start"},
    }
    assert process_update(conn, cfg, client, update) == "ignored"
    assert client.messages
    assert "42" in client.messages[0][1]
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_telegram_receiver_uses_fresh_db_connections_for_poll_and_each_update(
    conn, config, monkeypatch
):
    import logging
    import sqlite3
    import threading

    from worker import db
    from worker import telegram_ingest

    cfg = configured(config)
    stop_event = threading.Event()
    opened = []
    real_connect = db.connect

    def tracked_connect(path):
        connection = real_connect(path)
        opened.append(connection)
        return connection

    class PollingTelegram(FakeTelegram):
        def __init__(self, timeout=60):
            super().__init__()
            self.polls = 0

        def get_updates(self, token, offset=None, timeout=20):
            self.polls += 1
            assert offset is None
            return [{
                "update_id": 500,
                "message": {
                    "text": "/start",
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 42},
                },
            }]

        def send_message(self, token, chat_id, text, reply_markup=None):
            self.messages.append((chat_id, text, reply_markup))
            stop_event.set()
            return {"message_id": 1}

    client = PollingTelegram()
    monkeypatch.setattr(telegram_ingest.db, "connect", tracked_connect)
    monkeypatch.setattr(telegram_ingest, "TelegramClient", lambda timeout=60: client)

    telegram_ingest.run_forever(cfg, logging.getLogger("telegram-test"), stop_event)

    # One short-lived connection reads the polling offset and a separate one handles
    # /start. Both must be closed when the receiver exits.
    assert len(opened) == 2
    assert len(client.messages) == 1
    keyboard = client.messages[0][2]["inline_keyboard"]
    buttons = [button for row in keyboard for button in row]
    assert any(button["callback_data"] == "bs:accounts" for button in buttons)
    assert any(button["callback_data"] == "bs:account:add" for button in buttons)
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_telegram_receiver_retries_same_update_after_sqlite_busy(
    conn, config, monkeypatch
):
    import logging
    import sqlite3
    import threading

    from worker import telegram_ingest
    from worker import telegram_control

    cfg = configured(config)
    stop_event = threading.Event()
    original_handler = telegram_control.process_control_update
    attempts = []

    def busy_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_handler(*args, **kwargs)

    class RetryingTelegram(FakeTelegram):
        def __init__(self, timeout=60):
            super().__init__()
            self.polls = 0

        def get_updates(self, token, offset=None, timeout=20):
            self.polls += 1
            assert offset is None
            return [{
                "update_id": 501,
                "message": {
                    "text": "/start",
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 42},
                },
            }]

        def send_message(self, token, chat_id, text, reply_markup=None):
            self.messages.append((chat_id, text, reply_markup))
            stop_event.set()
            return {"message_id": 1}

    client = RetryingTelegram()
    monkeypatch.setattr(telegram_ingest, "TelegramClient", lambda timeout=60: client)
    monkeypatch.setattr(telegram_control, "process_control_update", busy_once)

    telegram_ingest.run_forever(cfg, logging.getLogger("telegram-retry-test"), stop_event)

    assert client.polls == 2
    assert len(attempts) == 2
    assert len(client.messages) == 1
    buttons = [
        button
        for row in client.messages[0][2]["inline_keyboard"]
        for button in row
    ]
    assert any(button["callback_data"] == "bs:accounts" for button in buttons)
