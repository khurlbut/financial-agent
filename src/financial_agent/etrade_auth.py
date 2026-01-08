from __future__ import annotations

import argparse
import os

from . import settings
from .etrade_client import ETradeClient
from .etrade_store import save_etrade_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize E*Trade (OAuth1) and store a local access token")
    parser.add_argument(
        "--container-id",
        default="etrade",
        help="Logical container id (e.g., 'kev', 'deb', 'joint').",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="E*Trade environment: 'sandbox' or 'prod' (defaults to ETRADE_ENV or 'sandbox').",
    )
    parser.add_argument(
        "--callback-url",
        default=None,
        help="OAuth callback URL (defaults to ETRADE_CALLBACK_URL or 'oob').",
    )

    args = parser.parse_args()

    container_id = (args.container_id or "").strip() or "etrade"
    env = (args.env or settings.get_etrade_environment()).strip().lower()

    base_url = settings.get_etrade_base_url()
    if args.env and not (os.getenv("ETRADE_BASE_URL") or "").strip():
        # Best-effort override when the user explicitly chose an env.
        base_url = "https://api.etrade.com" if env in {"prod", "production"} else "https://apisb.etrade.com"

    client = ETradeClient(base_url=base_url)

    req = client.get_request_token(callback_url=args.callback_url)
    print("Open this URL to authorize E*Trade access:")
    print(req.authorize_url)
    print("\nAfter approving, paste the verifier code here.")
    verifier = input("verifier: ").strip()
    if not verifier:
        raise SystemExit("No verifier provided")

    access_token, access_secret = client.exchange_access_token(
        oauth_token=req.oauth_token,
        oauth_token_secret=req.oauth_token_secret,
        verifier=verifier,
    )

    save_etrade_token(
        container_id=container_id,
        oauth_token=access_token,
        oauth_token_secret=access_secret,
        environment=env,
    )

    print(f"Saved E*Trade token for container_id={container_id!r} to {settings.get_etrade_tokens_path()}")


if __name__ == "__main__":
    main()
