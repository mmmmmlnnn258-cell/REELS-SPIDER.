"""Receive authorized Telegram videos and enqueue them as Instagram Reels.

The receiver is deliberately fail-closed: it does nothing until both a bot token and
an explicit chat allow-list are configured. It never guesses a channel by display name.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .telegram_api import TelegramAPIError, TelegramClient


class IngestError(Exception):
    pass


class NoEligibleInstagramChannel(IngestError):
    pass


def _allowed_chat_ids(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _select_channel(conn, configured_id: int | None):
    """Prefer an explicitly configured default; otherwise first connected active IG.

    A channel is eligible only when it is active AND has both the remote Instagram ID
    and access token. This prevents an unconnected test/draft channel being selected.
    """
    eligible = """
        SELECT * FROM channels
         WHERE platform = 'instagram' AND is_active = 1
           AND remote_account_id IS NOT NULL AND TRIM(remote_account_id) <> ''
           AND access_token IS NOT NULL AND TRIM(access_token) <> ''
    """
    if configured_id is not None:
        channel = conn.execute(
            eligible + " AND id = ?", (configured_id,)
        ).fetchone()
        if channel is None:
            raise NoEligibleInstagramChannel(
                "The configured default Instagram channel is missing, inactive, or not connected."
            )
        return channel
    channel = conn.execute(eligible + " ORDER BY id ASC LIMIT 1").fetchone()
    if channel is None:
        raise NoEligibleInstagramChannel(
            "No connected, active Instagram channel is available. Connect an Instagram channel first."
        )
    return channel


def _video_from_message(message: dict[str, Any]):
    video = message.get("video")
    if isinstance(video, dict) and video.get("file_id"):
        return video
    document = message.get("document")
    if (
        isinstance(document, dict)
        and document.get("file_id")
        and str(document.get("mime_type", "")).lower() in
        {"video/mp4", "video/quicktime"}
    ):
        return document
    return None


def _iter_mp4_boxes(data: bytes, start: int, end: int):
    """Yield (fourcc, payload_start, payload_end) for ISO-BMFF boxes."""
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8].decode("ascii", errors="ignore")
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _find_mp4_box(data: bytes, start: int, end: int, wanted: str):
    containers = {"moov", "trak", "mdia", "minf", "stbl", "udta"}
    for kind, payload_start, payload_end in _iter_mp4_boxes(data, start, end):
        if kind == wanted:
            return payload_start, payload_end
        if kind in containers:
            found = _find_mp4_box(data, payload_start, payload_end, wanted)
            if found:
                return found
    return None


def _read_video_metadata(data: bytes) -> tuple[int, int, int, int]:
    """Read duration/dimensions/audio from MP4/MOV headers without external binaries."""
    moov = _find_mp4_box(data, 0, len(data), "moov")
    if not moov:
        raise IngestError("The video container has no readable movie metadata.")
    mvhd = _find_mp4_box(data, moov[0], moov[1], "mvhd")
    if not mvhd:
        raise IngestError("The video duration could not be read.")
    p, end = mvhd
    version = data[p]
    if version == 1 and p + 32 <= end:
        timescale = int.from_bytes(data[p + 20:p + 24], "big")
        duration = int.from_bytes(data[p + 24:p + 32], "big")
    elif p + 20 <= end:
        timescale = int.from_bytes(data[p + 12:p + 16], "big")
        duration = int.from_bytes(data[p + 16:p + 20], "big")
    else:
        raise IngestError("The video duration metadata is incomplete.")
    if not timescale:
        raise IngestError("The video duration metadata is invalid.")
    duration_ms = int(duration * 1000 / timescale)

    width = height = 0
    has_audio = 0
    for kind, trak_start, trak_end in _iter_mp4_boxes(data, moov[0], moov[1]):
        if kind != "trak":
            continue
        hdlr = _find_mp4_box(data, trak_start, trak_end, "hdlr")
        if hdlr and hdlr[0] + 12 <= hdlr[1]:
            handler = data[hdlr[0] + 8:hdlr[0] + 12]
            if handler == b"soun":
                has_audio = 1
        if not hdlr or data[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
            continue
        tkhd = _find_mp4_box(data, trak_start, trak_end, "tkhd")
        if not tkhd:
            continue
        tp, te = tkhd
        version = data[tp]
        matrix_offset, dimensions_offset, minimum = (
            (52, 88, 96) if version == 1 else (40, 76, 84)
        )
        if te - tp < minimum:
            continue
        w = int.from_bytes(data[tp + dimensions_offset:tp + dimensions_offset + 4], "big") / 65536
        h = int.from_bytes(data[tp + dimensions_offset + 4:tp + dimensions_offset + 8], "big") / 65536
        if not w or not h:
            continue
        a = int.from_bytes(data[tp + matrix_offset:tp + matrix_offset + 4], "big", signed=True) / 65536
        b = int.from_bytes(data[tp + matrix_offset + 4:tp + matrix_offset + 8], "big", signed=True) / 65536
        c = int.from_bytes(data[tp + matrix_offset + 12:tp + matrix_offset + 16], "big", signed=True) / 65536
        d = int.from_bytes(data[tp + matrix_offset + 16:tp + matrix_offset + 20], "big", signed=True) / 65536
        if abs(a) < 0.01 and abs(d) < 0.01 and abs(b) > 0.01 and abs(c) > 0.01:
            w, h = h, w
        width, height = int(round(w)), int(round(h))
        break
    if duration_ms <= 0 or width <= 0 or height <= 0:
        raise IngestError("The video duration or dimensions could not be read.")
    return duration_ms, width, height, has_audio


def _record_update(conn, update_id: int, chat_id: str | None, status: str,
                   error: str | None = None, post_id: int | None = None) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO telegram_ingest_updates
           (update_id, chat_id, status, error, post_id)
           VALUES (?, ?, ?, ?, ?)""",
        (update_id, chat_id, status, error, post_id),
    )
    conn.commit()


def process_update(conn, config, client: TelegramClient, update: dict[str, Any],
                   logger: logging.Logger | None = None) -> str:
    """Process one Bot API update once. Returns processed, ignored, or failed."""
    update_id = int(update["update_id"])
    if conn.execute(
        "SELECT 1 FROM telegram_ingest_updates WHERE update_id = ?", (update_id,)
    ).fetchone():
        return "ignored"

    message = update.get("message") or update.get("channel_post")
    if not isinstance(message, dict):
        _record_update(conn, update_id, None, "ignored")
        return "ignored"

    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    allowed = _allowed_chat_ids(config.telegram_allowed_chat_ids)
    if not allowed and str(message.get("text") or "").strip().startswith("/start"):
        _record_update(conn, update_id, chat_id or None, "ignored")
        client.send_message(
            config.telegram_bot_token, chat_id,
            f"رقم محادثتك هو: {chat_id}. أضفه إلى TELEGRAM_ALLOWED_CHAT_IDS في إعدادات السيرفر لتفعيل استقبال الفيديوهات.",
        )
        return "ignored"
    if not chat_id or chat_id not in allowed:
        _record_update(conn, update_id, chat_id or None, "ignored")
        if logger and chat_id:
            logger.info("Ignored Telegram update from unapproved chat_id=%s", chat_id)
        return "ignored"

    video = _video_from_message(message)
    if not video:
        _record_update(conn, update_id, chat_id, "ignored")
        client.send_message(
            config.telegram_bot_token, chat_id,
            "أرسل فيديو بصيغة MP4 أو MOV حتى أجهزه للنشر على Instagram.",
        )
        return "ignored"

    try:
        channel = _select_channel(conn, config.telegram_default_instagram_channel_id)
        reported_size = int(video.get("file_size") or 0)
        if reported_size and reported_size > config.telegram_max_file_bytes:
            raise IngestError(
                f"Video is larger than the configured {config.telegram_max_file_bytes}-byte limit."
            )

        file_info = client.get_file(config.telegram_bot_token, str(video["file_id"]))
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            raise IngestError("Telegram did not return a downloadable file path.")
        file_bytes = client.download_file(
            config.telegram_bot_token, file_path, config.telegram_max_file_bytes
        )
        if len(file_bytes) > config.telegram_max_file_bytes:
            raise IngestError("Downloaded video exceeds the configured size limit.")
        if len(file_bytes) < 12 or file_bytes[4:8] != b"ftyp":
            raise IngestError("The received file is not a supported MP4/MOV video.")

        mime = str(video.get("mime_type") or mimetypes.guess_type(file_path)[0] or "video/mp4")
        extension = Path(file_path).suffix.lower()
        if extension not in {".mp4", ".mov"}:
            extension = ".mov" if mime == "video/quicktime" else ".mp4"
        duration_ms, width, height, has_audio = _read_video_metadata(file_bytes)
        digest = hashlib.sha256(file_bytes).hexdigest()
        filename = f"{digest}{extension}"
        config.asset_storage_dir.mkdir(parents=True, exist_ok=True)
        stored_path = config.asset_storage_dir / filename
        if not stored_path.exists():
            temporary = stored_path.with_suffix(stored_path.suffix + ".part")
            temporary.write_bytes(file_bytes)
            os.replace(temporary, stored_path)

        caption = str(message.get("caption") or "").strip() or None
        now = datetime.now(timezone.utc).isoformat()
        status = "pending_approval" if channel["requires_approval"] else "scheduled"

        with conn:
            existing = conn.execute(
                "SELECT id FROM assets WHERE content_hash = ?", (digest,)
            ).fetchone()
            if existing:
                asset_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """INSERT INTO assets
                       (content_hash, media_kind, original_filename, storage_path,
                        public_url, mime_type, width, height, byte_size, duration_ms, has_audio)
                       VALUES (?, 'video', ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (digest, filename, filename, mime, width, height, len(file_bytes),
                     duration_ms, has_audio),
                )
                asset_id = int(cur.lastrowid)

            cur = conn.execute(
                """INSERT INTO posts
                   (caption, post_type, status, content_kind, content_status, created_by)
                   VALUES (?, 'video', 'scheduled', 'one_time', 'ready', 'telegram')""",
                (caption,),
            )
            post_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO post_assets (post_id, asset_id, sort_order) VALUES (?, ?, 0)",
                (post_id, asset_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO post_targets (post_id, channel_id) VALUES (?, ?)",
                (post_id, int(channel["id"])),
            )
            if caption:
                conn.execute(
                    """INSERT INTO caption_variants (post_id, platform, body, sort_order)
                       VALUES (?, NULL, ?, 0)""",
                    (post_id, caption),
                )
            conn.execute(
                """INSERT INTO publications
                   (post_id, channel_id, scheduled_at, status, created_by, surface, asset_id)
                   VALUES (?, ?, ?, ?, 'telegram', 'feed', ?)""",
                (post_id, int(channel["id"]), now, status, asset_id),
            )
            conn.execute(
                """INSERT INTO telegram_ingest_updates
                   (update_id, chat_id, status, post_id)
                   VALUES (?, ?, 'processed', ?)""",
                (update_id, chat_id, post_id),
            )

        if logger:
            logger.info(
                "Telegram video ingested: update_id=%s post_id=%s channel_id=%s status=%s",
                update_id, post_id, channel["id"], status,
            )
        client.send_message(
            config.telegram_bot_token, chat_id,
            f"تم استلام الفيديو وتجهيزه. رقم المنشور: {post_id}",
        )
        return "processed"
    except Exception as exc:  # noqa: BLE001 - record and report a failed update, don't poison polling
        error = str(exc)[:500]
        _record_update(conn, update_id, chat_id, "failed", error)
        if logger:
            logger.error("Telegram update %s failed: %s", update_id, error)
        try:
            client.send_message(
                config.telegram_bot_token, chat_id,
                "تعذر تجهيز الفيديو. راجع إعدادات القناة وسجل تشغيل السيرفر.",
            )
        except Exception:
            if logger:
                logger.warning("Could not send Telegram failure notice for update %s", update_id)
        return "failed"


def run_forever(config, logger: logging.Logger, stop_event: threading.Event, client_for=None) -> None:
    token = config.telegram_bot_token.strip()
    allowed = _allowed_chat_ids(config.telegram_allowed_chat_ids)
    if not token:
        logger.info("Telegram video receiver disabled: TELEGRAM_BOT_TOKEN is not configured.")
        return
    if not allowed:
        logger.warning(
            "Telegram receiver is in discovery-only mode: send /start to the bot to learn "
            "your chat ID. Video uploads remain disabled until TELEGRAM_ALLOWED_CHAT_IDS is set."
        )

    client = TelegramClient(timeout=max(45, config.telegram_poll_timeout + 15))
    conn = db.connect(config.database_path)
    logger.info("Telegram video receiver started; allowed chat count=%d", len(allowed))
    try:
        while not stop_event.is_set():
            row = conn.execute(
                "SELECT MAX(update_id) AS last_id FROM telegram_ingest_updates"
            ).fetchone()
            offset = int(row["last_id"]) + 1 if row and row["last_id"] is not None else None
            try:
                updates = client.get_updates(
                    token, offset=offset, timeout=config.telegram_poll_timeout
                )
                for update in updates or []:
                    if stop_event.is_set():
                        break
                    from .telegram_control import process_control_update
                    if process_control_update(conn, config, client, update, graph_client_for=client_for):
                        message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
                        chat = message.get("chat") or {}
                        _record_update(conn, int(update["update_id"]), str(chat.get("id", "")) or None, "ignored")
                    else:
                        process_update(conn, config, client, update, logger)
            except TelegramAPIError as exc:
                logger.error("Telegram polling failed: %s", exc)
                stop_event.wait(5)
            except Exception:
                logger.exception("Unexpected Telegram receiver error")
                stop_event.wait(5)
    finally:
        conn.close()
        logger.info("Telegram video receiver stopped.")
