from worker.graph_api import GraphClient


class FakeResponse:
    ok = True
    status_code = 200
    text = "{}"
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.responses = []

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, params, timeout))
        return FakeResponse(self.responses.pop(0))

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append((url, json, headers, timeout))
        return FakeResponse(self.responses.pop(0))


def test_instagram_messaging_graph_routes_and_payloads():
    session = FakeSession()
    session.responses = [
        {"data": [{"id": "conversation-1"}]},
        {"id": "conversation-1", "participants": {"data": [{"id": "peer-1"}]},
         "messages": {"data": [{"id": "message-1", "message": "hello"}]}},
        {"message_id": "sent-1"},
    ]
    graph = GraphClient("v25.0", session=session, base_url="https://graph.instagram.com")

    conversations = graph.get_instagram_conversations("ig-account-1", "access-token")
    assert conversations == [{"id": "conversation-1"}]
    url, params, _ = session.get_calls[0]
    assert url == "https://graph.instagram.com/v25.0/ig-account-1/conversations"
    assert params["platform"] == "instagram"
    assert params["access_token"] == "access-token"

    conversation, messages = graph.get_instagram_conversation_messages(
        "conversation-1", "access-token"
    )
    assert conversation["id"] == "conversation-1"
    assert messages == [{"id": "message-1", "message": "hello"}]
    url, params, _ = session.get_calls[1]
    assert url == "https://graph.instagram.com/v25.0/conversation-1"
    assert "messages{" in params["fields"]

    result = graph.send_instagram_direct_message(
        "ig-account-1", "peer-1", "manual reply", "access-token"
    )
    assert result["message_id"] == "sent-1"
    url, payload, headers, _ = session.post_calls[0]
    assert url == "https://graph.instagram.com/v25.0/ig-account-1/messages"
    assert payload == {"recipient": {"id": "peer-1"}, "message": {"text": "manual reply"}}
    assert headers["Authorization"] == "Bearer access-token"


def test_instagram_direct_message_rejects_empty_recipient_or_text():
    graph = GraphClient("v25.0", session=FakeSession(), base_url="https://graph.instagram.com")
    for recipient, message in (("", "hello"), ("peer-1", ""), ("peer-1", "   ")):
        try:
            graph.send_instagram_direct_message("ig-account-1", recipient, message, "token")
        except ValueError:
            pass
        else:
            raise AssertionError("empty recipient/message must be rejected before HTTP")


def test_instagram_conversation_id_is_encoded_as_an_opaque_path_segment():
    session = FakeSession()
    session.responses = [
        {"id": "t_opaque/id?cursor=1", "participants": {"data": []},
         "messages": {"data": []}},
    ]
    graph = GraphClient("v25.0", session=session, base_url="https://graph.instagram.com")
    conversation, messages = graph.get_instagram_conversation_messages(
        "t_opaque/id?cursor=1", "access-token"
    )
    assert conversation["id"] == "t_opaque/id?cursor=1"
    assert messages == []
    assert session.get_calls[0][0] == (
        "https://graph.instagram.com/v25.0/t_opaque%2Fid%3Fcursor%3D1"
    )
