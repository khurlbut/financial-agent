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

- Schwab CSV download automation (Playwright) work is preserved on branch `schwab-direct`.
- The checkpoint tag for that work is `schwab-direct-csv-checkpoint-20260104`.

## Branch Notes (Current Work)

- The branch `schwab-manual` uses a manual CSV download flow: you download Schwab Positions CSV files yourself, and this repo imports them into SQLite for the API.

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

If you want Schwab holdings without an aggregator, this repo supports a local “manual download CSV + import” workflow.

- Download a Schwab Positions CSV manually.
- Import it into the local SQLite DB:
	- `python -m financial_agent.schwab_refresh --csv /path/to/positions.csv`
	- or (imports all `*.csv` in a directory): `python -m financial_agent.schwab_refresh --csv-dir downloads`

The imported data is surfaced via the API as container source `schwab` with container id `schwab`.

### Multiple Schwab Logins (Containers)

If you have multiple Schwab logins (e.g., yours and your spouse’s), model each login as its own Schwab **container**.

Example:

- Your login: `container_id=kev`
- Spouse login: `container_id=deb`

Import each login’s CSVs with `--container-id`:

- `python -m financial_agent.schwab_refresh --container-id kev --csv-dir downloads/kev`
- `python -m financial_agent.schwab_refresh --container-id deb --csv-dir downloads/deb`

Within each container, individual Schwab accounts (ROTH, Brokerage, etc.) are exposed as `account_id` values.

### Live Pricing vs CSV Pricing (Schwab)

By default, Schwab CSV imports include price and market value columns, and the API will use those values.

If you want *positions-only* from the CSV (quantity) and *live pricing* at request time, set:

- `FINAGENT_SCHWAB_CSV_PRICE_MODE=live`

In live mode, the Schwab CSV provider omits CSV `price` and `market_value` for non-cash assets, which forces the valuation layer to request prices from the active pricing provider.

Pricing provider options:

- `FINAGENT_PRICE_PROVIDER=coinbase` (default): good for crypto; equities will likely show up in `missing_prices`.
- `FINAGENT_PRICE_PROVIDER=stooq`: no-key equities/ETFs pricing via stooq.com (often delayed/EOD).
- `FINAGENT_PRICE_PROVIDER=composite`: tries Coinbase first, then stooq.com (recommended if you have both crypto + equities).

## Morgan Stanley (CSV Refresh)

This repo also supports a manual “download CSV + import” workflow for Morgan Stanley.

- Download an account positions/holdings CSV manually.
- Import it into the local SQLite DB:
	- `python -m financial_agent.morgan_stanley_refresh --csv /path/to/positions.csv`
	- or (imports all `*.csv` in a directory): `python -m financial_agent.morgan_stanley_refresh --csv-dir downloads/morgan_stanley`

The imported data is surfaced via the API as container source `morgan_stanley` with container ids based on `--container-id` (e.g. `kev`, `deb`).

### Live Pricing vs CSV Pricing (Morgan Stanley)

By default, Morgan Stanley CSV imports are treated as *positions-only* and priced live:

- `FINAGENT_MORGAN_STANLEY_CSV_PRICE_MODE=live` (default)

If you want to trust the CSV’s `price` and/or `market_value` columns instead, set:

- `FINAGENT_MORGAN_STANLEY_CSV_PRICE_MODE=csv`
