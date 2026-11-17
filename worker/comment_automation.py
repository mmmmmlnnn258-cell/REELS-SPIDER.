"""Opt-in Instagram Reel comment automation using official Meta API edges.

Meta currently documents Reels as unsupported for Instagram comments webhooks, so
configured Reels are polled. Comment IDs are claimed before sending to prevent duplicate
private replies after polling overlap or worker restarts. Ambiguous send failures are
marked unknown and are never retried automatically.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

POLL_INTERVAL_SECONDS = 120
MAX_COMMENT_PAGES = 3


def _matches(text: str, keywords: list[str]) -> bool:
    candidate = (text or "").casefold()
    for raw in keywords:
        keyword = str(raw).strip().casefold()
        if keyword and re.search(r"(?<!\\w)" + re.escape(keyword) + r"(?!\\w)", candidate):
            return True
    return False


def _render(template: str, *, link: str, username: str, media_id: str) -> str:
    return (template or "").replace("{link}", link or "").replace(
        "{username}", username or ""
    ).replace("{media_id}", media_id)


def _claim_event(conn: sqlite3.Connection, comment_id: str, rule: sqlite3.Row,
                 channel_id: int, media_id: str, comment_text: str, now_iso: str) -> bool:
    try:
        conn.execute(
            """INSERT INTO instagram_comment_events
               (comment_id, rule_id, channel_id, media_id, comment_text, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'processing', ?, ?)""",
            (comment_id, rule["id"], channel_id, media_id, comment_text, now_iso, now_iso),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def run_comment_automation(conn, config, now=None, logger=None, client_for=None) -> int:
    """Poll enabled Reel rules and send at most one private reply per comment."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    rows = conn.execute(
        """SELECT r.*, c.access_token, c.remote_account_id, c.platform, c.account_name
           FROM instagram_comment_rules r
           JOIN channels c ON c.id = r.channel_id
           WHERE r.enabled = 1 AND c.is_active = 1 AND c.platform = 'instagram'
             AND c.access_token IS NOT NULL AND c.remote_account_id IS NOT NULL"""
    ).fetchall()
    sent = 0
    for rule in rows:
        if rule["last_polled_at"]:
            try:
                last = datetime.fromisoformat(rule["last_polled_at"])
                if (now - last).total_seconds() < POLL_INTERVAL_SECONDS:
                    continue
            except (TypeError, ValueError):
                pass
        try:
            client = client_for("instagram") if client_for else None
            if client is None:
                continue
            token, account_id, media_id = (
                rule["access_token"], str(rule["remote_account_id"]), str(rule["media_id"])
            )
            page_url = None
            for _ in range(MAX_COMMENT_PAGES):
                comments, page_url = client.get_media_comments(
                    media_id, token, limit=50, next_url=page_url
                )
                for comment in comments:
                    comment_id = str(comment.get("id") or "")
                    if not comment_id:
                        continue
                    author = comment.get("from") or {}
                    if str(author.get("id") or "") == account_id or comment.get("parent_id"):
                        continue
                    text = str(comment.get("text") or "")
                    username = str(author.get("username") or comment.get("username") or "")
                    if not _claim_event(conn, comment_id, rule, rule["channel_id"],
                                        media_id, text, now_iso):
                        continue
                    keywords = json.loads(rule["keywords_json"] or "[]")
                    if not _matches(text, keywords):
                        conn.execute(
                            "UPDATE instagram_comment_events SET status='ignored', updated_at=? WHERE comment_id=?",
                            (now_iso, comment_id),
                        )
                        conn.commit()
                        continue
                    stamp = comment.get("timestamp")
                    if stamp:
                        try:
                            created = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                            if created.tzinfo is None:
                                created = created.replace(tzinfo=timezone.utc)
                            if now - created > timedelta(days=7):
                                conn.execute(
                                    "UPDATE instagram_comment_events SET status='expired', updated_at=? WHERE comment_id=?",
                                    (now_iso, comment_id),
                                )
                                conn.commit()
                                continue
                        except ValueError:
                            pass
                    link = str(rule["link_url"] or "")
                    private_text = _render(rule["private_reply_text"] or "", link=link,
                                            username=username, media_id=media_id)
                    public_text = _render(rule["public_reply_text"] or "", link=link,
                                           username=username, media_id=media_id)
                    private_id = public_id = None
                    try:
                        if private_text.strip():
                            result = client.send_private_reply(account_id, comment_id, private_text, token)
                            private_id = result.get("message_id")
                        if public_text.strip():
                            public_id = client.reply_to_comment(comment_id, public_text, token)
                        status, error = "sent", None
                        if private_id or public_id:
                            sent += 1
                    except Exception as exc:  # noqa: BLE001
                        from .redact import redact
                        status, error = "unknown", redact(str(exc))[:500]
                        if logger:
                            logger.warning("[comment %s] reply outcome unknown: %s", comment_id, error)
                    conn.execute(
                        """UPDATE instagram_comment_events
                           SET status=?, private_reply_id=?, public_reply_id=?, last_error=?, updated_at=?
                           WHERE comment_id=?""",
                        (status, private_id, public_id, error, now_iso, comment_id),
                    )
                    conn.commit()
                if not page_url:
                    break
            conn.execute(
                "UPDATE instagram_comment_rules SET last_polled_at=?, last_error=NULL WHERE id=?",
                (now_iso, rule["id"]),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            from .redact import redact
            error = redact(str(exc))[:500]
            conn.execute(
                "UPDATE instagram_comment_rules SET last_polled_at=?, last_error=? WHERE id=?",
                (now_iso, error, rule["id"]),
            )
            conn.commit()
            if logger:
                logger.warning("[comment rule %s] polling failed: %s", rule["id"], error)
    return sent
