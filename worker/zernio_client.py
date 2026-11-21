"""Small, secret-safe client for the Zernio REST API.

The API key is read only from ZERNIO_API_KEY. Never include request headers,
credentials, or raw response bodies in exceptions or logs.
"""
from __future__ import annotations

import os
import requests


API_BASE_URL = "https://zernio.com/api/v1"
DEFAULT_TIMEOUT = 20


class ZernioError(RuntimeError):
    """Sanitized Zernio API or transport failure."""


class ZernioClient:
    def __init__(self, api_key: str | None = None, *, session=None,
                 base_url: str = API_BASE_URL, timeout: int = DEFAULT_TIMEOUT):
        self._api_key = (api_key if api_key is not None else os.getenv("ZERNIO_API_KEY", "")).strip()
        if not self._api_key:
            raise ZernioError("ZERNIO_API_KEY is not configured.")
        self._session = session or requests
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _request(self, method: str, path: str, *, params=None, json=None, headers=None):
        url = self._base_url + "/" + path.lstrip("/")
        try:
            response = self._session.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    **(headers or {}),
                },
                params=params,
                json=json,
                timeout=self._timeout,
            )
        except requests.RequestException:
            raise ZernioError("Could not reach Zernio. Check the network and try again.") from None
        if response.status_code < 200 or response.status_code >= 300:
            # Do not surface provider response bodies: they can contain account data.
            if response.status_code == 401:
                message = "Zernio rejected the API key. Check ZERNIO_API_KEY."
            elif response.status_code == 402:
                message = "The current Zernio plan does not allow this operation."
            elif response.status_code == 403:
                message = "Zernio denied access to this profile or account."
            elif response.status_code == 404:
                message = "The requested Zernio resource was not found."
            elif response.status_code == 409:
                message = "A matching Zernio resource already exists."
            elif response.status_code == 429:
                message = "Zernio rate limit reached. Try again later."
            else:
                message = f"Zernio request failed (HTTP {response.status_code})."
            raise ZernioError(message)
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            raise ZernioError("Zernio returned an invalid response.") from None
        if not isinstance(payload, dict):
            raise ZernioError("Zernio returned an invalid response.")
        return payload

    def list_profiles(self, *, name: str | None = None) -> list[dict]:
        params = {"name": name} if name else None
        payload = self._request("GET", "/profiles", params=params)
        profiles = payload.get("profiles", [])
        if not isinstance(profiles, list):
            raise ZernioError("Zernio returned an invalid profiles list.")
        return [item for item in profiles if isinstance(item, dict)]

    def create_profile(self, name: str, *, idempotency_key: str | None = None) -> str:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        payload = self._request("POST", "/profiles", headers=headers, json={
            "name": name,
            "description": "BLACK SPIDER SocialScheduler",
        })
        profile = payload.get("profile") or {}
        profile_id = profile.get("_id") or profile.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ZernioError("Zernio did not return a profile ID.")
        return profile_id

    def ensure_profile(self, name: str = "BLACK-SPIDER") -> str:
        profiles = self.list_profiles(name=name)
        for profile in profiles:
            profile_id = profile.get("_id") or profile.get("id")
            if isinstance(profile_id, str) and profile_id:
                return profile_id
        try:
            return self.create_profile(name)
        except ZernioError as exc:
            # A concurrent worker may have created the same uniquely named profile.
            if "already exists" not in str(exc):
                raise
            for profile in self.list_profiles(name=name):
                profile_id = profile.get("_id") or profile.get("id")
                if isinstance(profile_id, str) and profile_id:
                    return profile_id
            raise

    def instagram_connect_url(self, profile_id: str, redirect_url: str) -> str:
        if not profile_id or not redirect_url.startswith("https://"):
            raise ZernioError("A valid Zernio profile ID and HTTPS callback URL are required.")
        payload = self._request(
            "GET",
            "/connect/instagram",
            params={"profileId": profile_id, "redirect_url": redirect_url},
        )
        auth_url = payload.get("authUrl")
        if not isinstance(auth_url, str) or not auth_url.startswith("https://"):
            raise ZernioError("Zernio did not return a valid Instagram authorization URL.")
        return auth_url

    def upload_media(self, path, *, media_type: str | None = None) -> dict:
        """Upload a local media file using Zernio's presigned URL flow."""
        from pathlib import Path
        import mimetypes

        file_path = Path(path)
        if not file_path.is_file():
            raise ZernioError("Media file is missing from local storage.")
        mime_type = mimetypes.guess_type(file_path.name)[0]
        if not mime_type or mime_type == "application/octet-stream":
            raise ZernioError("Media file has no supported MIME type.")
        size = file_path.stat().st_size
        presign = self._request("POST", "/media/presign", json={
            "filename": file_path.name,
            "contentType": mime_type,
            "size": size,
        })
        upload_url = presign.get("uploadUrl")
        public_url = presign.get("publicUrl")
        if not isinstance(upload_url, str) or not upload_url.startswith("https://") or not isinstance(public_url, str) or not public_url.startswith("https://"):
            raise ZernioError("Zernio did not return valid media upload URLs.")
        try:
            with file_path.open("rb") as stream:
                response = self._session.put(upload_url, data=stream, headers={"Content-Type": mime_type}, timeout=self._timeout)
        except requests.RequestException:
            raise ZernioError("Could not upload media to Zernio storage.") from None
        if response.status_code < 200 or response.status_code >= 300:
            raise ZernioError(f"Zernio media storage rejected the upload (HTTP {response.status_code}).")
        return {"url": public_url, "type": "video" if mime_type.startswith("video/") else "image"}

    def create_post(self, *, content: str | None, account_id: str, media_items: list[dict],
                    content_type: str | None = None, first_comment: str | None = None,
                    idempotency_key: str) -> dict:
        """Create a single-account Instagram post with retry-safe request identity."""
        if not account_id or not media_items:
            raise ZernioError("A connected Instagram account and at least one media item are required.")
        body = {
            "content": content or "",
            "mediaItems": media_items,
            "platforms": [{"platform": "instagram", "accountId": account_id}],
            "publishNow": True,
        }
        if content_type in ("reel", "story"):
            body["contentType"] = content_type
        if first_comment and content_type != "story":
            body["firstComment"] = first_comment
        payload = self._request("POST", "/posts", json=body, headers={"Idempotency-Key": idempotency_key})
        post = payload.get("post")
        if not isinstance(post, dict):
            raise ZernioError("Zernio did not return a post record.")
        platforms = post.get("platforms")
        if not isinstance(platforms, list) or not platforms or not isinstance(platforms[0], dict):
            raise ZernioError("Zernio did not return the Instagram publishing result.")
        result = platforms[0]
        status = str(result.get("status") or post.get("status") or "").lower()
        if status not in ("published", "success", "completed"):
            reason = result.get("error") or result.get("message") or post.get("message")
            raise ZernioError("Zernio did not confirm publication" + (f": {str(reason)[:240]}" if reason else "."))
        remote_id = result.get("platformPostId") or result.get("postId")
        if not isinstance(remote_id, str) or not remote_id:
            remote_id = post.get("_id")
        if not isinstance(remote_id, str) or not remote_id:
            raise ZernioError("Zernio confirmed publication without returning a post ID.")
        return {"remote_post_id": remote_id, "zernio_post_id": str(post.get("_id") or ""), "status": status}

    def list_accounts(self, *, profile_id: str) -> list[dict]:
        if not profile_id:
            raise ZernioError("A Zernio profile ID is required.")
        payload = self._request("GET", "/accounts", params={"profileId": profile_id})
        accounts = payload.get("accounts", [])
        if not isinstance(accounts, list):
            raise ZernioError("Zernio returned an invalid accounts list.")
        return [item for item in accounts if isinstance(item, dict)]
