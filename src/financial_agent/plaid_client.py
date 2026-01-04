from __future__ import annotations

from typing import Any

from . import settings


def get_plaid_client() -> Any:
    """Create a Plaid client.

    Uses the official Plaid Python SDK.
    Import is done lazily so the rest of the codebase can run without Plaid
    installed until you enable this provider.
    """

    creds = settings.get_plaid_credentials()

    try:
        from plaid.api import plaid_api
        from plaid.api_client import ApiClient
        from plaid.configuration import Configuration
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Plaid SDK not installed or import failed: {exc}")

    # Plaid currently exposes two API environments: sandbox and production.
    # "development" is treated as an alias of production for backward compatibility.
    env_map = {
        "sandbox": "https://sandbox.plaid.com",
        "production": "https://production.plaid.com",
        "development": "https://production.plaid.com",
    }

    configuration = Configuration(
        host=env_map[creds.environment],
        api_key={
            "clientId": creds.client_id,
            "secret": creds.secret,
        },
    )

    api_client = ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def create_link_token(*, client_name: str = "financial-agent", country_codes: list[str] | None = None) -> dict:
    """Create a Plaid Link token for local single-user linking."""

    try:
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
        from plaid.model.products import Products
        from plaid.model.country_code import CountryCode
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Plaid SDK not installed or import failed: {exc}")

    plaid = get_plaid_client()

    creds = settings.get_plaid_credentials()
    redirect_uri = settings.get_plaid_redirect_uri()
    if creds.environment == "production" and redirect_uri and not redirect_uri.startswith("https://"):
        raise RuntimeError(
            "PLAID_REDIRECT_URI must use HTTPS in production. "
            "Either unset PLAID_REDIRECT_URI (if your institution doesn't require OAuth), "
            "or use an HTTPS URL (e.g., via ngrok/cloudflared) and register it in the Plaid Dashboard."
        )

    kwargs: dict[str, Any] = {
        "user": LinkTokenCreateRequestUser(client_user_id=settings.get_plaid_user_id()),
        "client_name": client_name,
        "products": [Products("investments")],
        "country_codes": [CountryCode(c) for c in (country_codes or ["US"])],
        "language": "en",
    }
    if redirect_uri:
        # Only include redirect_uri when set; the Plaid SDK model rejects None.
        kwargs["redirect_uri"] = redirect_uri

    req = LinkTokenCreateRequest(**kwargs)

    resp = plaid.link_token_create(req)
    # SDK returns a model; best-effort to dict.
    if hasattr(resp, "to_dict"):
        return resp.to_dict()  # type: ignore[no-any-return]
    return dict(resp)


def exchange_public_token(*, public_token: str) -> dict:
    """Exchange a public_token for an access_token + item_id."""

    try:
        from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Plaid SDK not installed or import failed: {exc}")

    plaid = get_plaid_client()

    req = ItemPublicTokenExchangeRequest(public_token=public_token)
    resp = plaid.item_public_token_exchange(req)
    if hasattr(resp, "to_dict"):
        return resp.to_dict()  # type: ignore[no-any-return]
    return dict(resp)


def get_investments_holdings(*, access_token: str) -> dict:
    """Fetch investments holdings + securities for an Item."""

    try:
        from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Plaid SDK not installed or import failed: {exc}")

    plaid = get_plaid_client()

    req = InvestmentsHoldingsGetRequest(access_token=access_token)
    resp = plaid.investments_holdings_get(req)
    if hasattr(resp, "to_dict"):
        return resp.to_dict()  # type: ignore[no-any-return]
    return dict(resp)
