# financial-agent

## Install

From the repo root:

- Runtime install: `pip install .`
- Dev/test install: `pip install -e ".[dev]"`

Run tests:

- `python -m pytest`

If you’re using the repo’s `.local_profile`, you can also run tests with: `t`

## Preferred AI Endpoint: `/agent/portfolio`

If you’re building an AI agent (or any client that needs both total portfolio value and per-asset sizing), use:

- `GET /agent/portfolio`

This endpoint returns a *valued snapshot* plus server-computed rollups so your client doesn’t need to re-implement aggregation logic.

Example:

```bash
curl -s http://127.0.0.1:8000/agent/portfolio | python -m json.tool
```

Key fields in the response:

- `total_value`: total USD value (cash + priced positions)
- `cash_value`, `positions_value`: breakdown of total value
- `by_asset`: aggregated view per asset (quantity, price, market value) including per-account breakdowns (container + account)
- `by_account`: per-account totals (accounts live *within* a container)
- `by_container`: per-container totals (e.g., Coinbase, a cold-storage device)
- `missing_prices`: assets with balances that could not be priced (assets in `FINAGENT_IGNORED_ASSETS` are omitted)

## Branch Notes (Archived Work)

- Plaid (Schwab via Plaid Link) integration work is preserved on branch `schwab-plaid`.
- The last known-good checkpoint on that line of work is the git tag `plaid-schwab-checkpoint`.

## Independent Queries (Net Worth / Containers / Holdings)

If your client prefers to query these concepts independently (instead of consuming the full `/agent/portfolio` payload), use:

- Total net worth (aggregate across all sources):
	- `GET /agent/networth`
- List all brokerages/exchanges/devices (“containers”) with their total value:
	- `GET /agent/containers`
- Discover which pricing provider is active (Coinbase/Binance/etc.):
	- `GET /agent/pricing`
- Get total value for a single container:
	- `GET /agent/container/value?source=coinbase&container_id=coinbase`
	- `GET /agent/container/value?source=cold_storage&container_id=<device name>`
- List accounts within a container (for brokers with multiple accounts):
	- `GET /agent/container/accounts?source=coinbase&container_id=coinbase`
- Get holdings for a single container (includes cash + positions):
	- `GET /agent/container/holdings?source=coinbase&container_id=coinbase`
	- `GET /agent/container/holdings?source=cold_storage&container_id=<device name>`

You can optionally scope container endpoints to a specific account:
	- `GET /agent/container/value?source=coinbase&container_id=coinbase&account_id=<account uuid>`
	- `GET /agent/container/holdings?source=coinbase&container_id=coinbase&account_id=<account uuid>`

## Schwab (CSV Refresh)

If you want Schwab holdings without an aggregator, this repo supports a local “download CSV + import” workflow.

- Install Playwright and the browser:
	- `pip install -r requirements.txt`
	- `python -m playwright install chromium`
- Refresh Schwab holdings (headful; you may need to complete login/MFA manually):
	- `python -m financial_agent.schwab_refresh`

This downloads a positions CSV into `downloads/` and imports it into a local SQLite DB (`financial_agent.sqlite3`).
The imported data is surfaced via the API as container source `schwab` with container id `schwab`.
