"""Unit tests for the secret-safe Zernio REST client."""
import pytest

from worker.zernio_client import ZernioClient, ZernioError


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        return self.responses.pop(0)


def test_instagram_connect_uses_hosted_oauth_and_bearer_auth():
    session = Session([Response(payload={"authUrl": "https://www.instagram.com/oauth/authorize?state=x"})])
    client = ZernioClient("private-key", session=session)
    url = client.instagram_connect_url("profile-1", "https://example.com/callback")
    assert url.startswith("https://www.instagram.com/")
    method, endpoint, kwargs = session.calls[0]
    assert method == "GET"
    assert endpoint.endswith("/connect/instagram")
    assert kwargs["params"] == {
        "profileId": "profile-1",
        "redirect_url": "https://example.com/callback",
    }
    assert kwargs["headers"]["Authorization"] == "Bearer private-key"


def test_ensure_profile_reuses_existing_profile():
    session = Session([Response(payload={"profiles": [{"_id": "existing-profile", "name": "BLACK-SPIDER"}]})])
    client = ZernioClient("private-key", session=session)
    assert client.ensure_profile() == "existing-profile"
    assert len(session.calls) == 1


def test_list_accounts_filters_to_profile():
    session = Session([Response(payload={"accounts": [{"_id": "acct-1", "platform": "instagram"}]})])
    client = ZernioClient("private-key", session=session)
    assert client.list_accounts(profile_id="profile-1")[0]["_id"] == "acct-1"
    assert session.calls[0][2]["params"] == {"profileId": "profile-1"}


def test_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    with pytest.raises(ZernioError, match="ZERNIO_API_KEY"):
        ZernioClient()


def test_error_does_not_expose_response_body_or_api_key():
    session = Session([Response(status=401, payload={"error": "private-key leaked"})])
    client = ZernioClient("private-key", session=session)
    with pytest.raises(ZernioError) as error:
        client.list_profiles()
    assert "private-key" not in str(error.value)


def test_create_profile_sends_idempotency_key():
    session = Session([Response(payload={"profile": {"_id": "new-profile"}})])
    client = ZernioClient("private-key", session=session)
    assert client.create_profile("BLACK-SPIDER", idempotency_key="setup-once") == "new-profile"
    assert session.calls[0][0] == "POST"
    assert session.calls[0][2]["headers"]["Idempotency-Key"] == "setup-once"

    
def test_create_post_is_publish_now_and_idempotent():
    session = Session([Response(payload={
        "post": {
            "_id": "zernio-post-1",
            "status": "published",
            "platforms": [{"platform": "instagram", "status": "published", "platformPostId": "ig-post-1"}],
        }
    })])
    client = ZernioClient("private-key", session=session)
    result = client.create_post(
        content="Caption",
        account_id="ig-account-1",
        media_items=[{"url": "https://media.zernio.com/file.jpg", "type": "image"}],
        first_comment="More details below",
        idempotency_key="black-spider-publication-42",
    )
    assert result["remote_post_id"] == "ig-post-1"
    method, url, kwargs = session.calls[0]
    assert method == "POST" and url.endswith("/posts")
    assert kwargs["headers"]["Idempotency-Key"] == "black-spider-publication-42"
    assert kwargs["json"]["publishNow"] is True
    assert kwargs["json"]["platforms"] == [{
        "platform": "instagram", "accountId": "ig-account-1",
        "platformSpecificData": {"firstComment": "More details below"},
    }]
    assert "firstComment" not in kwargs["json"]


def test_upload_media_uses_presigned_put_without_api_key(tmp_path):
    media = tmp_path / "sample.jpg"
    media.write_bytes(b"image-bytes")
    session = Session([
        Response(payload={
            "uploadUrl": "https://storage.example/upload?signature=secret",
            "publicUrl": "https://media.zernio.com/sample.jpg",
        }),
        Response(status=200),
    ])
    client = ZernioClient("private-key", session=session)
    result = client.upload_media(media)
    assert result == {"url": "https://media.zernio.com/sample.jpg", "type": "image"}
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/media/presign")
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer private-key"
    assert session.calls[1][0] == "PUT"
    assert "Authorization" not in session.calls[1][2]["headers"]
    assert session.calls[1][2]["headers"]["Content-Type"] == "image/jpeg"


def test_list_inbox_conversations_is_scoped_to_instagram_account():
    session = Session([Response(payload={"data": [{"id": "thread-1", "platform": "instagram"}]})])
    client = ZernioClient("private-key", session=session)
    rows = client.list_inbox_conversations(account_id="ig-account", updated_since="2026-11-20T10:00:00Z")
    assert rows[0]["id"] == "thread-1"
    assert session.calls[0][1].endswith("/inbox/conversations")
    assert session.calls[0][2]["params"]["accountId"] == "ig-account"
    assert session.calls[0][2]["params"]["platform"] == "instagram"
    assert session.calls[0][2]["params"]["updatedSince"] == "2026-11-20T10:00:00Z"


def test_list_inbox_messages_quotes_opaque_conversation_id():
    session = Session([Response(payload={"messages": [{"id": "m1", "direction": "incoming"}]})])
    client = ZernioClient("private-key", session=session)
    rows = client.list_inbox_messages("thread/with space", account_id="ig-account")
    assert rows[0]["id"] == "m1"
    assert "/thread%2Fwith%20space/messages" in session.calls[0][1]
    assert session.calls[0][2]["params"]["accountId"] == "ig-account"


def test_send_inbox_message_requires_idempotency_and_uses_bearer():
    session = Session([Response(payload={"success": True, "message": {"id": "sent-1"}})])
    client = ZernioClient("private-key", session=session)
    result = client.send_inbox_message(
        "thread-1", account_id="ig-account", message="Hello",
        idempotency_key="black-spider-human-reply-17",
    )
    assert result["message"]["id"] == "sent-1"
    method, url, kwargs = session.calls[0]
    assert method == "POST" and url.endswith("/thread-1/messages")
    assert kwargs["headers"]["Authorization"] == "Bearer private-key"
    assert kwargs["headers"]["Idempotency-Key"] == "black-spider-human-reply-17"
    assert kwargs["json"] == {"accountId": "ig-account", "message": "Hello"}


def test_create_comment_automation_requires_profile_and_returns_id():
    session = Session([Response(payload={"success": True, "automation": {"id": "automation-1"}})])
    client = ZernioClient("private-key", session=session)
    automation = client.create_comment_automation({
        "profileId": "profile-1", "accountId": "ig-account",
        "name": "BLACK SPIDER rule 4", "keywords": ["price"],
        "dmMessage": "Here is the information", "isActive": False,
    }, idempotency_key="black-spider-rule-4")
    assert automation["id"] == "automation-1"
    assert session.calls[0][1].endswith("/comment-automations")
    assert session.calls[0][2]["headers"]["Idempotency-Key"] == "black-spider-rule-4"


def test_list_comment_automations_is_profile_scoped():
    session = Session([Response(payload={"automations": [{"id": "automation-1"}]})])
    client = ZernioClient("private-key", session=session)
    assert client.list_comment_automations(profile_id="profile-1")[0]["id"] == "automation-1"
    assert session.calls[0][2]["params"] == {"profileId": "profile-1"}


def test_story_content_type_is_nested_in_instagram_platform_data():
    session = Session([Response(payload={
        "post": {
            "_id": "zernio-post-story",
            "status": "published",
            "platforms": [{"platform": "instagram", "status": "published", "platformPostUrl": "https://instagram.com/stories/example"}],
        }
    })])
    client = ZernioClient("private-key", session=session)
    result = client.create_post(
        content=None, account_id="ig-account-1",
        media_items=[{"url": "https://media.zernio.com/story.jpg", "type": "image"}],
        content_type="story", idempotency_key="black-spider-story-7",
    )
    assert result["remote_post_id"] == "https://instagram.com/stories/example"
    body = session.calls[0][2]["json"]
    assert body["platforms"][0]["platformSpecificData"] == {"contentType": "story"}
    assert "contentType" not in body


def test_list_post_comments_is_scoped_to_zernio_instagram_account():
    session = Session([Response(payload={"comments": [{"id": "comment-1", "text": "Hello"}]})])
    client = ZernioClient("private-key", session=session)
    rows = client.list_post_comments("media-1", account_id="ig-account-1")
    assert rows[0]["id"] == "comment-1"
    assert session.calls[0][1].endswith("/inbox/comments/media-1")
    assert session.calls[0][2]["params"]["accountId"] == "ig-account-1"
    assert session.calls[0][2]["params"]["platform"] == "instagram"


def test_list_comment_automation_logs_returns_delivery_and_keyword_miss_data():
    session = Session([Response(payload={
        "logs": [{"id": "log-1", "status": "sent", "commenterName": "Customer"}],
        "misses": {"total": 3, "retentionDays": 7, "samples": []},
        "pagination": {"total": 1, "hasMore": False},
    })])
    client = ZernioClient("private-key", session=session)
    result = client.list_comment_automation_logs("automation-1", limit=10, skip=0)
    assert result["logs"][0]["status"] == "sent"
    assert result["misses"]["total"] == 3
    assert session.calls[0][1].endswith("/comment-automations/automation-1/logs")
    assert session.calls[0][2]["params"] == {"limit": 10, "skip": 0}
