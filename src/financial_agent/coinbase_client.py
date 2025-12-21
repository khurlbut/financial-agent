from typing import Any, Dict, List
import os
from pathlib import Path

from dotenv import load_dotenv
from coinbase.rest import RESTClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

API_KEY_ID = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")

if API_SECRET:
    # Turn the literal backslash-n sequences into real newlines for PEM parsing
    API_SECRET = API_SECRET.replace("\\n", "\n")


class CoinbaseClient:
    def __init__(self) -> None:
        if not (API_KEY_ID and API_SECRET):
            raise RuntimeError("COINBASE_API_KEY or COINBASE_API_SECRET not set in environment")

        self._client = RESTClient(api_key=API_KEY_ID, api_secret=API_SECRET)

    def list_accounts(self) -> List[Dict[str, Any]]:
        resp = self._client.get_accounts()  # GET /api/v3/brokerage/accounts

        if isinstance(resp, dict):
            return resp.get("accounts", [])

        accounts = getattr(resp, "accounts", [])
        return [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in accounts]
