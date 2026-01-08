from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from . import settings


@dataclass(frozen=True)
class OAuthRequestToken:
    oauth_token: str
    oauth_token_secret: str
    authorize_url: str


class ETradeClient:
    """Minimal E*Trade API client.

    Uses OAuth1 (3-legged) for user-authorized access.

    This client is intentionally small and focused on read-only account + portfolio data.
    """

    def __init__(
        self,
        *,
        consumer_key: str | None = None,
        consumer_secret: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        creds = settings.get_etrade_credentials() if consumer_key is None and consumer_secret is None else None
        self._consumer_key = consumer_key or (creds.consumer_key if creds else "")
        self._consumer_secret = consumer_secret or (creds.consumer_secret if creds else "")
        self._base_url = (base_url or settings.get_etrade_base_url()).rstrip("/")
        self._timeout_s = timeout_s

    @property
    def base_url(self) -> str:
        return self._base_url

    def get_request_token(self, *, callback_url: str | None = None) -> OAuthRequestToken:
        from requests_oauthlib import OAuth1Session

        cb = callback_url or settings.get_etrade_callback_url()
        sess = OAuth1Session(
            client_key=self._consumer_key,
            client_secret=self._consumer_secret,
            callback_uri=cb,
        )

        # E*Trade OAuth1 endpoints.
        request_token_url = f"{self._base_url}/oauth/request_token"
        authorize_url = f"{self._base_url}/oauth/authorize"

        resp = sess.fetch_request_token(request_token_url)
        tok = resp.get("oauth_token")
        sec = resp.get("oauth_token_secret")
        if not isinstance(tok, str) or not isinstance(sec, str) or not tok or not sec:
            raise RuntimeError("E*Trade request_token response missing oauth_token/oauth_token_secret")

        full_authorize_url = sess.authorization_url(authorize_url)
        return OAuthRequestToken(oauth_token=tok, oauth_token_secret=sec, authorize_url=full_authorize_url)

    def exchange_access_token(
        self,
        *,
        oauth_token: str,
        oauth_token_secret: str,
        verifier: str,
    ) -> tuple[str, str]:
        """Exchange request token for an access token."""

        from requests_oauthlib import OAuth1Session

        sess = OAuth1Session(
            client_key=self._consumer_key,
            client_secret=self._consumer_secret,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_token_secret,
            verifier=verifier,
        )
        access_token_url = f"{self._base_url}/oauth/access_token"
        resp = sess.fetch_access_token(access_token_url)
        tok = resp.get("oauth_token")
        sec = resp.get("oauth_token_secret")
        if not isinstance(tok, str) or not isinstance(sec, str) or not tok or not sec:
            raise RuntimeError("E*Trade access_token response missing oauth_token/oauth_token_secret")
        return tok, sec

    def _oauth_session(self, *, oauth_token: str, oauth_token_secret: str):
        from requests_oauthlib import OAuth1Session

        return OAuth1Session(
            client_key=self._consumer_key,
            client_secret=self._consumer_secret,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_token_secret,
        )

    def get_json(self, *, oauth_token: str, oauth_token_secret: str, path: str, params: dict[str, Any] | None = None) -> Any:
        sess = self._oauth_session(oauth_token=oauth_token, oauth_token_secret=oauth_token_secret)
        url = f"{self._base_url}{path}"
        r = sess.get(url, params=params, headers={"Accept": "application/json"}, timeout=self._timeout_s)
        r.raise_for_status()
        return r.json()

    def list_accounts(self, *, oauth_token: str, oauth_token_secret: str) -> Any:
        # Best-effort endpoint path.
        return self.get_json(
            oauth_token=oauth_token,
            oauth_token_secret=oauth_token_secret,
            path="/v1/accounts/list.json",
        )

    def get_portfolio(self, *, oauth_token: str, oauth_token_secret: str, account_id_key: str) -> Any:
        return self.get_json(
            oauth_token=oauth_token,
            oauth_token_secret=oauth_token_secret,
            path=f"/v1/accounts/{account_id_key}/portfolio.json",
        )

    def get_balance(self, *, oauth_token: str, oauth_token_secret: str, account_id_key: str) -> Any:
        return self.get_json(
            oauth_token=oauth_token,
            oauth_token_secret=oauth_token_secret,
            path=f"/v1/accounts/{account_id_key}/balance.json",
        )
