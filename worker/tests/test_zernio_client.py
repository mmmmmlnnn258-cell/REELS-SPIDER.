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
