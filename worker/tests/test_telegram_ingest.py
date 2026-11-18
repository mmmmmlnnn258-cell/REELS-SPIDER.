from __future__ import annotations

from dataclasses import replace

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
