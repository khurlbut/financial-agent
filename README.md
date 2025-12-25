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
- `by_asset`: aggregated view per asset (quantity, price, market value) including per-account breakdowns for consolidation/rebalancing workflows
- `by_account`: per-account totals with holdings (cash + positions)
- `missing_prices`: assets with balances that could not be priced (assets in `FINAGENT_IGNORED_ASSETS` are omitted)

## Independent Queries (Net Worth / Containers / Holdings)

If your client prefers to query these concepts independently (instead of consuming the full `/agent/portfolio` payload), use:

- Total net worth (aggregate across all sources):
	- `GET /agent/networth`
- List all brokerages/exchanges/devices (“containers”) with their total value:
	- `GET /agent/containers`
- Get total value for a single container:
	- `GET /agent/container/value?source=coinbase&account_id=coinbase`
	- `GET /agent/container/value?source=cold_storage&account_id=<device name>`
- Get holdings for a single container (includes cash + positions):
	- `GET /agent/container/holdings?source=coinbase&account_id=coinbase`
	- `GET /agent/container/holdings?source=cold_storage&account_id=<device name>`