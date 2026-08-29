# Portfolio CLI

The `portfolio` command creates validated portfolio metadata, bootstraps an
opening snapshot, and imports ongoing broker activity. See the
[storage contract](README.md), [schema reference](schemas.md), and
[import-file contracts](import-files.md) for validation rules.

## Create a portfolio

```shell
mise run py-fund-manager -- \
  portfolio create brokerage \
  --broker historical \
  --account-id brokerage-123
```

The command creates `portfolio/brokerage/portfolio.yaml` in the
root selected by the required [global configuration](cli.md#data-root).
`--broker` identifies the broker adapter or source, while `--account-id`
preserves the broker's identifier for the account; neither value is inferred
from the portfolio ID. Commands discover the manifest by `kind: Portfolio`, so
the filename can be changed without changing resource identity. An existing
directory is accepted only when it contains tracked scaffolding named
`README.md` or `.gitignore`; any portfolio data or other entry makes creation
fail before files are written.

Create the opening ledger directly when the balances are already known:

```shell
mise run py-fund-manager -- \
  portfolio create playground \
  --broker historical \
  --account-id playground \
  --as-of 2020-01-02T08:00:00-08:00 \
  --balance=USD:10000,AMAT:22
```

`--balance` accepts comma-separated `ASSET:VALUE` pairs. The asset matching the
portfolio base currency is opening cash and follows its currency precision and
size limits; every other asset is an opening position whose value is its
quantity. Assets are normalized to uppercase and may appear only once. Inline
balances write `transactions.csv` directly and do not create a preserved source
below `imports/`.

## Import an opening snapshot

Bootstrap a new portfolio from canonical positions and cash during creation:

```shell
mise run py-fund-manager -- \
  portfolio create brokerage \
  --broker historical \
  --account-id brokerage-123 \
  --as-of 2020-01-02T08:00:00-08:00 \
  --balance=@/path/to/private/opening.csv
```

The CSV uses `amount` for the opening balance and `quantity` for security
positions. See the [Import Files reference](import-files.md#opening-snapshot-csv)
for its complete column schema and validation rules. The command validates and
preserves the source, then writes one ledger row per opening fact. `--as-of` is
the broker statement's effective timestamp and must include a timezone offset;
without it, the import time is used. Existing imports and transaction ledgers
are not replaced.

## Import broker activity

Append confirmed events after the opening boundary:

```shell
mise run py-fund-manager -- \
  portfolio import brokerage \
  /path/to/private/activity-2020-03.csv
```

Every event carries its own timestamp and stable source identity. Identical events
from overlapping exports are skipped; conflicting reuse of an identity fails the
import. Dividend reinvestment is recorded as a dividend followed by a buy. See
the [Activity CSV contract](import-files.md#activity-csv) for the complete schema
and append rules.

## Rebalance a portfolio

The command uses the strategy assignment effective at the planning time, derived
holdings and cash, and cached daily closing prices to produce a broker-neutral JSON
order plan.

Rebalancing treats existing portfolio cash as investable. It generates buy
orders that move cash into underweight strategy positions, leaving only any
residual caused by quantity rounding. Planning does not execute those orders or
modify the transaction ledger.

Plan a rebalance without adding or removing cash:

```shell
mise run py-fund-manager -- \
  portfolio rebalance brokerage
```

Plan a rebalance after contributing USD 10,000:

```shell
mise run py-fund-manager -- \
  portfolio rebalance brokerage \
  --contribute 10000.00
```

Plan a USD 5,000 withdrawal and the sales needed to fund it:

```shell
mise run py-fund-manager -- \
  portfolio rebalance brokerage \
  --withdraw 5000.00
```

`--contribute` and `--withdraw` accept nonnegative amounts with at most 18 integer
digits and two decimal places in the portfolio's base currency. They are mutually
exclusive planning assumptions, not confirmed cash transactions.

Select a historical planning time with an ISO 8601 timestamp containing a
timezone offset. The default is the current time:

```shell
mise run py-fund-manager -- \
  portfolio rebalance brokerage \
  --as-of 2026-08-26T14:00:00-07:00
```

Prices come from the latest eligible `interval=1d` close. A daily close becomes
eligible at 16:00 in the exchange timezone stored in its Parquet partition, so a
same-day close is not used earlier in that trading day. Price, date, availability
time, currency, provider, and source partition are selected together. Missing
prices or required metadata fail the plan; observations older than the planning
date produce a warning. Yahoo-style class-share symbols map strategy dots to
price-cache hyphens, such as `BRK.B` to `BRK-B`. All transaction and price
currencies must match the portfolio's base currency.

### Price refresh workflow

The current rebalance command reads the local price cache; it does not contact the
price provider. “Latest eligible” therefore means the latest qualifying close in
`stocks-by-ticker/`, not necessarily the latest close published by the market.
Use the [download command](cli-download.md) to refresh daily prices before
rebalancing. A cached observation older than the planning date currently produces
a warning rather than failing the plan.

The planned integrated refresh workflow is:

1. Resolve the portfolio's effective strategy and derive its current holdings.
1. Build the required ticker set from the union of strategy positions and current
   holdings, including holdings that must be sold because they are absent from the
   strategy.
1. Refresh `interval=1d` data for the current year. Also refresh the previous year
   when it may contain the latest completed session, such as immediately after a
   year boundary.
1. Download tickers concurrently with a bounded worker pool. Write each successful
   yearly partition atomically and preserve existing successful partitions when
   another ticker or year fails.
1. Determine the expected latest completed trading session from an exchange
   calendar and the exchange timezone. Apply a provider-publication delay after
   the session close rather than assuming the final bar exists exactly at 16:00.
1. Validate that every required ticker has a coherent price, observation date,
   availability time, currency, provider, retrieval time, and source partition for
   that expected session.
1. Fail planning if any required observation is missing, conflicting, or stale.
   A future explicit `--allow-stale-prices` option may permit a reviewed exception;
   stale data must never be accepted implicitly.
1. Generate the plan only after the complete price set passes validation. Include
   the selected price provenance in every order as the current schema requires.

This workflow will use the downloader and rebalance planner as shared application
services rather than duplicating Yahoo Finance requests in the CLI dispatcher.

The command writes the JSON order plan to standard output so it can be saved and
reviewed:

```shell
mise run py-fund-manager -- \
  portfolio rebalance brokerage > rebalance-plan.json
```

Execute a reviewed plan against cached historical prices at an explicit time:

```shell
mise run py-fund-manager -- \
  broker historical rebalance-plan.json \
  --as-of 2026-08-26T14:00:00-07:00 \
  > executions-2026-08-26.json
```

The broker command loads the portfolio named by the plan and its current ledger.
Before submitting orders, it verifies that the plan still matches the portfolio,
cash, holdings, and price-availability boundary. It then validates complete fills,
nonnegative resulting cash, and resulting positions. Confirmed executions are
written as JSON but are not appended automatically; convert them to the canonical
activity CSV and import that file after review.

Plans containing `--contribute` cannot be executed because they assume cash that
is not yet in the ledger. Record the completed deposit and generate a new plan.
A plan containing `--withdraw` can be executed: its sell orders raise the
reserved cash, and execution fails unless the confirmed fills leave at least the
planned withdrawal amount available. Import those fills before recording the
confirmed withdrawal in the ledger.

Under the documented strict rebalance policy, strategy positions missing from the
portfolio generate buys, while portfolio positions absent from the strategy have a
zero target and generate closing sells. The plan is advice: it neither submits
orders nor writes transactions. Only confirmed broker executions become ledger
transactions. Non-closing quantities assume fractional-share trading with a
six-decimal increment. Buys round down to avoid overspending; sells round up and
are capped at the current holding so withdrawals are fully funded without selling
more shares than the portfolio owns. Each estimated notional is the exact rounded
quantity times its estimated price. Summary amounts and estimated ending cash use
those exact notionals, preserving residual cash caused by quantity rounding.
Current and target valuation amounts are rounded to cents. Broker-specific
whole-share, minimum-order, tax-lot, and limit-price rules remain outside this
planner.

Run `mise run py-fund-manager -- portfolio --help` for current options.
