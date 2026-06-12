import logging
import time
from dataclasses import dataclass

import httpx

logger: logging.Logger = logging.getLogger(name=__name__)

_TOKEN_BUFFER_SECONDS = 60.0


@dataclass
class _AccessToken:
    value: str
    expires_at: float

    @property
    def soon_to_expire(self) -> bool:
        return time.monotonic() >= self.expires_at


class EdGraphTokenRetriever:
    """Manages the lifecycle of an EdGraph OAuth2 access token.

    Lazily issues on the first call to get() and automatically refreshes before
    expiry, using a 60-second buffer to avoid 401 responses mid-request.
    """

    def __init__(self, identity_url: str, client_id: str, client_secret: str) -> None:
        self._identity_url: str = identity_url
        self._client_id: str = client_id
        self._client_secret: str = client_secret
        self._token: _AccessToken | None = None

    def get(self) -> str:
        if self._token is None or self._token.soon_to_expire:
            self._token: _AccessToken = self._fetch()
        return self._token.value

    def _fetch(self) -> _AccessToken:
        logger.info("Requesting access token from %s.", self._identity_url)
        response: httpx.Response = httpx.post(
            url=f"{self._identity_url}/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()

        payload: dict = response.json()
        token: str | None = payload.get("access_token")

        if not token:
            raise ValueError("Access token not found in response.")

        expires_in = float(payload.get("expires_in", 3600))
        logger.info("Successfully obtained access token.")
        return _AccessToken(
            value=token,
            expires_at=time.monotonic() + expires_in - _TOKEN_BUFFER_SECONDS,
        )
