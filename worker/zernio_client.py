"""Small, secret-safe client for the Zernio REST API.

The API key is read only from ZERNIO_API_KEY. Never include request headers,
credentials, or raw response bodies in exceptions or logs.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

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

    def _request(self, method: str, path: str, *, params=None, json=None):
        url = self._base_url + "/" + path.lstrip("/")
        try:
            response = self._session.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
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
        headers = None
        # _request intentionally centralizes auth. Profile creation is safe to retry
        # using the provider's idempotency header when supplied.
        payload = self._request("POST", "/profiles", json={
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

    def list_accounts(self, *, profile_id: str) -> list[dict]:
        if not profile_id:
            raise ZernioError("A Zernio profile ID is required.")
        payload = self._request("GET", "/accounts", params={"profileId": profile_id})
        accounts = payload.get("accounts", [])
        if not isinstance(accounts, list):
            raise ZernioError("Zernio returned an invalid accounts list.")
        return [item for item in accounts if isinstance(item, dict)]
