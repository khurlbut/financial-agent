import base64
import hashlib
import hmac
import os
import time
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root explicitly
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

COINBASE_API_KEY = os.getenv("COINBASE_API_KEY")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET")
COINBASE_API_PASSPHRASE = os.getenv("COINBASE_API_PASSPHRASE")

BASE_URL = "https://api.coinbase.com/api/v3/brokerage"


class CoinbaseClient:
    def __init__(self) -> None:
        if not (COINBASE_API_KEY and COINBASE_API_SECRET and COINBASE_API_PASSPHRASE):
            raise RuntimeError("Coinbase API credentials are not set in environment")
        self._secret_bytes = base64.b64decode(COINBASE_API_SECRET)

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = f"{timestamp}{method.upper()}{request_path}{body}".encode("utf-8")
        signature = hmac.new(self._secret_bytes, message, hashlib.sha256).digest()
        return base64.b64encode(signature).decode("utf-8")

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """
        Calls GET /api/v3/brokerage/accounts and returns the 'accounts' list.
        """
        path = "/accounts"
        url = f"{BASE_URL}{path}"
        timestamp = str(int(time.time()))
        method = "GET"
        body = ""

        headers = {
            "CB-ACCESS-KEY": COINBASE_API_KEY,
            "CB-ACCESS-SIGN": self._sign(timestamp, method, path, body),
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-ACCESS-PASSPHRASE": COINBASE_API_PASSPHRASE,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Coinbase response shape: { "accounts": [...], "has_next": bool, ... }
        return data.get("accounts", [])
