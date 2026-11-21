"""Opt-in Instagram Reel comment automation using official Meta API edges.

Meta currently documents Reels as unsupported for Instagram comments webhooks, so
configured Reels are polled. Comment IDs are claimed before sending to prevent duplicate
private replies after polling overlap or worker restarts. Ambiguous send failures are
marked unknown and are never retried automatically.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone

POLL_INTERVAL_SECONDS = 120
MAX_COMMENT_PAGES = 3
# Meta's documented private-reply ceiling is 750/hour/account. Polling every 120s
# with this cap keeps one worker comfortably below that ceiling even across many rules.
MAX_PRIVATE_REPLIES_PER_CYCLE = 15
MAX_PRIVATE_REPLIES_PER_HOUR = 700


def _normalise_keyword(value: str) -> str:
    """Normalise Arabic diacritics/letter variants and punctuation for matching."""
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = "".join(
        char for char in value
        if char != "ـ" and unicodedata.category(char) != "Mn"
    )
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))
    return " ".join(re.findall(r"\w+", value))


def _matches(text: str, keywords: list[str]) -> bool:
    candidate = f" {_normalise_keyword(text)} "
    return any(
        (needle := _normalise_keyword(str(raw))) and f" {needle} " in candidate
        for raw in keywords
    )


def _render(template: str, *, link: str, username: str, media_id: str) -> str:
    return (template or "").replace("{link}", link or "").replace(
        "{username}", username or ""
    ).replace("{media_id}", media_id)


def _claim_event(conn: sqlite3.Connection, comment_id: str, rule: sqlite3.Row,
                 channel_id: int, media_id: str, comment_text: str, now_iso: str) -> bool:
    cursor = conn.execute(
        """INSERT OR IGNORE INTO instagram_comment_events
           (comment_id, rule_id, channel_id, media_id, comment_text, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'processing', ?, ?)""",
        (comment_id, rule["id"], channel_id, media_id, comment_text, now_iso, now_iso),
    )
    if cursor.rowcount:
        conn.commit()
        return True
    # Older versions stored non-matching comments as ignored. If the owner later edits
    # the rule's keywords, allow those comments to be evaluated again; sent/unknown/
    # processing events remain permanently deduplicated.
    existing = conn.execute(
        "SELECT rule_id, status FROM instagram_comment_events WHERE comment_id=?",
        (comment_id,),
    ).fetchone()
    if existing and existing["rule_id"] == rule["id"] and existing["status"] == "ignored":
        conn.execute(
            """UPDATE instagram_comment_events
               SET status='processing', comment_text=?, last_error=NULL, updated_at=?
               WHERE comment_id=?""",
            (comment_text, now_iso, comment_id),
        )
        conn.commit()
        return True
    return False



def _sync_zernio_rule(conn: sqlite3.Connection, rule, now_iso: str, logger=None) -> None:
    """Mirror one local rule into Zernio; Zernio owns delivery and deduplication."""
    automation_id = rule["zernio_automation_id"]
    if not rule["enabled"] and not automation_id:
        return

    try:
        if automation_id and rule["zernio_media_id"] and str(rule["zernio_media_id"]) != str(rule["media_id"]):
            raise RuntimeError("The rule's Instagram media binding changed; refusing to update the old Zernio automation.")
        if not rule["zernio_account_id"] or not rule["zernio_profile_id"]:
            raise RuntimeError("Zernio account/profile mapping is incomplete.")
        keywords = json.loads(rule["keywords_json"] or "[]")
        private_text = str(rule["private_reply_text"] or "").strip()
        if rule["enabled"] and (not keywords or not private_text):
            raise RuntimeError(
                "Zernio automation needs at least one keyword and a private reply."
            )

        def remote_template(value: str) -> str:
            return (value or "").replace("{link}", str(rule["link_url"] or "")).replace(
                "{media_id}", str(rule["media_id"] or "")
            ).replace("{username}", "{{username}}")

        body = {
            "name": f"BLACK SPIDER rule #{rule['id']}",
            "trigger": "comment",
            "platformPostId": str(rule["media_id"]),
            "keywords": [str(item) for item in keywords],
            "matchMode": "word",
            "dmMessage": remote_template(private_text),
            "commentReply": remote_template(str(rule["public_reply_text"] or "").strip()),
            "repeatPolicy": {"mode": "every_comment"},
            "publicReplyPolicy": "after_dm",
            "isActive": bool(rule["enabled"]),
        }

        fingerprint = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if fingerprint == rule["zernio_config_hash"] and automation_id:
            conn.execute(
                "UPDATE instagram_comment_rules SET last_polled_at=?, last_error=NULL WHERE id=?",
                (now_iso, rule["id"]),
            )
            conn.commit()
            return

        from .zernio_client import ZernioClient
        zernio = ZernioClient()
        if not automation_id and rule["enabled"]:
            # Reconcile after an ambiguous timeout before creating anything again.
            matches = [
                item for item in zernio.list_comment_automations(
                    profile_id=rule["zernio_profile_id"]
                )
                if item.get("name") == body["name"]
                and item.get("accountId") == rule["zernio_account_id"]
                and str(item.get("platformPostId") or "") == str(rule["media_id"])
            ]
            if len(matches) > 1:
                raise RuntimeError("Multiple remote automations match this BLACK SPIDER rule; refusing to choose.")
            if matches:
                automation_id = str(matches[0].get("id") or "")
                if not automation_id:
                    raise RuntimeError("Matching Zernio automation has no ID.")

        if automation_id:
            # PATCH accepts editable copy/keyword/active fields; the original post
            # binding and repeat policy remain as created.
            update_body = {
                key: body[key] for key in (
                    "name", "trigger", "keywords", "matchMode",
                    "dmMessage", "commentReply", "isActive",
                )
            }
            zernio.update_comment_automation(automation_id, update_body)
        elif rule["enabled"]:
            create_body = {
                **body,
                "profileId": rule["zernio_profile_id"],
                "accountId": rule["zernio_account_id"],
            }
            created = zernio.create_comment_automation(create_body)
            automation_id = created["id"]

        conn.execute(
            """UPDATE instagram_comment_rules
                  SET zernio_automation_id=?, zernio_media_id=?, zernio_config_hash=?,
                      last_polled_at=?, last_error=NULL
                WHERE id=?""",
            (automation_id, str(rule["media_id"]) if automation_id else None,
             fingerprint, now_iso, rule["id"]),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — keep provider details out of logs
        from .redact import redact
        error = redact(str(exc))[:500]
        conn.execute(
            "UPDATE instagram_comment_rules SET last_polled_at=?, last_error=? WHERE id=?",
            (now_iso, error, rule["id"]),
        )
        conn.commit()
        if logger:
            logger.warning("[Zernio comment rule %s] sync failed: %s", rule["id"], error)

def run_comment_automation(conn, config, now=None, logger=None, client_for=None) -> int:
    """Poll enabled Reel rules and send at most one private reply per comment."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    rows = conn.execute(
        """SELECT r.*, c.access_token, c.remote_account_id, c.platform, c.account_name,
                  c.instagram_auth_method, c.zernio_account_id, c.zernio_profile_id
           FROM instagram_comment_rules r
           JOIN channels c ON c.id = r.channel_id
           WHERE c.is_active = 1 AND c.platform = 'instagram'
             AND (
               (c.access_token IS NOT NULL AND c.remote_account_id IS NOT NULL)
               OR (c.zernio_account_id IS NOT NULL AND TRIM(c.zernio_account_id)<>'')
             )"""
    ).fetchall()
    sent = 0
    private_replies_this_cycle = 0
    hourly_private_counts: dict[int, int] = {}
    hour_cutoff = (now - timedelta(hours=1)).isoformat()
    stop_after_current_rule = False
    for rule in rows:
        if rule["zernio_account_id"]:
            _sync_zernio_rule(conn, rule, now_iso, logger=logger)
            continue
        if not rule["enabled"]:
            continue
        if rule["last_polled_at"]:
            try:
                last = datetime.fromisoformat(rule["last_polled_at"])
                if (now - last).total_seconds() < POLL_INTERVAL_SECONDS:
                    continue
            except (TypeError, ValueError):
                pass
        try:
            client = client_for(rule) if client_for else None
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
                    channel_id = int(rule["channel_id"])
                    if rule["private_reply_text"]:
                        if channel_id not in hourly_private_counts:
                            hourly_private_counts[channel_id] = conn.execute(
                                """SELECT COUNT(*) FROM instagram_comment_events
                                   WHERE channel_id=? AND created_at>=?
                                     AND status IN ('processing','sent','unknown')""",
                                (channel_id, hour_cutoff),
                            ).fetchone()[0]
                        if (
                            private_replies_this_cycle >= MAX_PRIVATE_REPLIES_PER_CYCLE
                            or hourly_private_counts[channel_id] >= MAX_PRIVATE_REPLIES_PER_HOUR
                        ):
                            stop_after_current_rule = True
                            break
                    comment_id = str(comment.get("id") or "")
                    if not comment_id:
                        continue
                    author = comment.get("from") or {}
                    if str(author.get("id") or "") == account_id or comment.get("parent_id"):
                        continue
                    text = str(comment.get("text") or "")
                    username = str(author.get("username") or comment.get("username") or "")
                    # Match before claiming: irrelevant comments should not be stored as
                    # processed, otherwise adding a new keyword later can never match them.
                    keywords = json.loads(rule["keywords_json"] or "[]")
                    if not _matches(text, keywords):
                        continue
                    if not _claim_event(conn, comment_id, rule, rule["channel_id"],
                                        media_id, text, now_iso):
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
                            private_replies_this_cycle += 1
                            hourly_private_counts[channel_id] = hourly_private_counts.get(channel_id, 0) + 1
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
                if stop_after_current_rule or not page_url:
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
        if stop_after_current_rule:
            break
    return sent
