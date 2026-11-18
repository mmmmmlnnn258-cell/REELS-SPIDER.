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
import time
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
    if not chat_id or chat_id not in allowed:
        _record_update(conn, update_id, chat_id or None, "ignored")
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
                        public_url, mime_type, byte_size)
                       VALUES (?, 'video', ?, ?, NULL, ?, ?)""",
                    (digest, filename, filename, mime, len(file_bytes)),
                )
                asset_id = int(cur.lastrowid)

            cur = conn.execute(
                """INSERT INTO posts
                   (caption, post_type, status, created_by)
                   VALUES (?, 'reel', 'scheduled', 'telegram')""",
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


def run_forever(config, logger: logging.Logger, stop_event: threading.Event) -> None:
    token = config.telegram_bot_token.strip()
    allowed = _allowed_chat_ids(config.telegram_allowed_chat_ids)
    if not token:
        logger.info("Telegram video receiver disabled: TELEGRAM_BOT_TOKEN is not configured.")
        return
    if not allowed:
        logger.error(
            "Telegram video receiver disabled: TELEGRAM_ALLOWED_CHAT_IDS is empty; "
            "configure an explicit chat allow-list before accepting uploads."
        )
        return

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
