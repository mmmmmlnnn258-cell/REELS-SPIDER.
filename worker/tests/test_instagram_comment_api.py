"""Contract tests for the Instagram comments and private-reply API methods."""
from worker.graph_api import GraphAPIError, GraphClient


class Response:
    ok = True
    status_code = 200
    text = ""
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.get_calls = []
        self.post_calls = []

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, params))
        return self.responses.pop(0)

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.post_calls.append((url, data, json, headers))
        return self.responses.pop(0)


def client(responses):
    return GraphClient("v25.0", session=Session(responses),
                       base_url="https://graph.instagram.com")


def test_get_media_comments_requests_top_level_comment_fields():
    c = client([Response({"data": [{"id": "c1", "text": "تم"}],
                          "paging": {"next": "https://graph.instagram.com/next"}})])
    comments, next_url = c.get_media_comments("media1", "token", limit=50)
    url, params = c.session.get_calls[0]
    assert url == "https://graph.instagram.com/v25.0/media1/comments"
    assert params["fields"] == "id,text,timestamp,username,from,parent_id"
    assert params["access_token"] == "token"
    assert comments == [{"id": "c1", "text": "تم"}]
    assert next_url == "https://graph.instagram.com/next"


def test_get_media_comments_follows_meta_pagination_url_verbatim():
    c = client([Response({"data": [{"id": "c2"}], "paging": {}})])
    comments, next_url = c.get_media_comments(
        "media1", "token", next_url="https://graph.instagram.com/next?cursor=abc"
    )
    assert c.session.get_calls == [
        ("https://graph.instagram.com/next?cursor=abc", None)
    ]
    assert comments == [{"id": "c2"}]
    assert next_url is None


def test_private_reply_uses_comment_id_recipient_and_bearer_token():
    c = client([Response({"message_id": "m1", "recipient_id": "person1"})])
    result = c.send_private_reply("ig-user", "comment1", "Here is the link", "secret")
    url, data, body, headers = c.session.post_calls[0]
    assert url == "https://graph.instagram.com/v25.0/ig-user/messages"
    assert body == {"recipient": {"comment_id": "comment1"},
                    "message": {"text": "Here is the link"}}
    assert headers == {"Authorization": "Bearer secret"}
    assert data is None
    assert result["message_id"] == "m1"


def test_public_reply_uses_comment_replies_edge():
    c = client([Response({"id": "reply1"})])
    assert c.reply_to_comment("comment1", "تم ❤️", "token") == "reply1"
    url, data, body, headers = c.session.post_calls[0]
    assert url == "https://graph.instagram.com/v25.0/comment1/replies"
    assert data == {"message": "تم ❤️", "access_token": "token"}
    assert body is None


def test_private_reply_rejects_blank_text_before_network_call():
    c = client([])
    try:
        c.send_private_reply("ig-user", "comment1", "  ", "token")
    except ValueError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("blank private reply should be rejected")
    assert c.session.post_calls == []
