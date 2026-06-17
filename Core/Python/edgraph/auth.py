import json
import logging
import time
import urllib.parse
from dataclasses import dataclass

import urllib3
from urllib3.response import BaseHTTPResponse

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
        self._http = urllib3.PoolManager()

    def close(self) -> None:
        self._http.clear()

    def get(self) -> str:
        if self._token is None or self._token.soon_to_expire:
            self._token: _AccessToken = self._fetch()
        return self._token.value

    def _fetch(self) -> _AccessToken:
        url = f"{self._identity_url}/connect/token"
        logger.info("Requesting access token from %s.", url)

        body: str = urllib.parse.urlencode(
            query={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        )
        response: BaseHTTPResponse = self._http.request(
            method="POST",
            url=url,
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=urllib3.Timeout(connect=10, read=10),
        )

        if response.status >= 400:
            error_body = response.data.decode("utf-8", errors="replace")
            raise ValueError(f"Token request failed with status {response.status}: {error_body}")

        payload: dict = json.loads(response.data.decode(encoding="utf-8"))
        token: str | None = payload.get("access_token")

        if not token:
            raise ValueError("Access token not found in response.")

        expires_in = float(payload.get("expires_in", 3600))
        logger.info("Successfully obtained access token.")
        logger.debug("Access token: %s", token)
        return _AccessToken(
            value=token,
            expires_at=time.monotonic() + expires_in - _TOKEN_BUFFER_SECONDS,
        )
