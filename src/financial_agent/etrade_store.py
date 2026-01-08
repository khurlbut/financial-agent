from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import settings


@dataclass(frozen=True)
class ETradeToken:
    oauth_token: str
    oauth_token_secret: str
    environment: str
    created_at: str | None = None


def load_etrade_tokens(path: Path | None = None) -> dict[str, ETradeToken]:
    # Keep tests hermetic: don't accidentally pick up a developer's real tokens
    # from the default project-root file during pytest runs.
    if path is None and os.getenv("PYTEST_CURRENT_TEST") and not (os.getenv("FINAGENT_ETRADE_TOKENS_PATH") or "").strip():
        return {}

    p = path or settings.get_etrade_tokens_path()
    if not p.exists():
        return {}

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    out: dict[str, ETradeToken] = {}
    for container_id, item in raw.items():
        if not isinstance(container_id, str) or not isinstance(item, dict):
            continue
        tok = item.get("oauth_token")
        sec = item.get("oauth_token_secret")
        env = item.get("environment")
        if not isinstance(tok, str) or not isinstance(sec, str) or not isinstance(env, str):
            continue
        out[container_id] = ETradeToken(
            oauth_token=tok,
            oauth_token_secret=sec,
            environment=env,
            created_at=item.get("created_at"),
        )

    return out


def get_etrade_token(*, container_id: str, path: Path | None = None) -> ETradeToken | None:
    return load_etrade_tokens(path=path).get(container_id)


def save_etrade_token(
    *,
    container_id: str,
    oauth_token: str,
    oauth_token_secret: str,
    environment: str,
    path: Path | None = None,
) -> None:
    p = path or settings.get_etrade_tokens_path()

    data: dict[str, dict] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    data[container_id] = {
        "oauth_token": oauth_token,
        "oauth_token_secret": oauth_token_secret,
        "environment": environment,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def delete_etrade_token(*, container_id: str, path: Path | None = None) -> bool:
    p = path or settings.get_etrade_tokens_path()
    if not p.exists():
        return False

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False

    if not isinstance(raw, dict) or container_id not in raw:
        return False

    raw.pop(container_id, None)
    p.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    return True
