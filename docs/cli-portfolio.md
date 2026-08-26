# Portfolio CLI

The `portfolio` command creates validated portfolio metadata and can bootstrap
opening positions from canonical holdings. See the [storage
contract](README.md) for schemas and validation rules.

## Create a portfolio

```shell
mise run py-fund-manager -- portfolio --create etrade-brokerage
```

The command creates `portfolios/etrade-brokerage/portfolio.yaml` below the
root selected by the required [global configuration](cli.md#data-root).

## Import opening positions

Bootstrap a new portfolio from canonical holdings during creation:

```shell
mise run py-fund-manager -- \
  portfolio --create etrade-brokerage \
  import-stocks /path/to/private/stocks.csv
```

The command validates and preserves the source CSV, then writes one
`opening_position` transaction per holding. Existing imports and transaction
ledgers are not replaced.

## Rebalance a portfolio

The command uses the strategy assignment effective at the planning time, derived
holdings and cash, and cached daily closing prices to produce a broker-neutral JSON
order plan.

Plan a rebalance without adding or removing cash:

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage rebalance
```

Plan a rebalance after contributing USD 10,000:

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage rebalance \
  --contribute 10000.00
```

Plan a USD 5,000 withdrawal and the sales needed to fund it:

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage rebalance \
  --withdraw 5000.00
```

`--contribute` and `--withdraw` accept nonnegative amounts in the portfolio's
base currency and are mutually exclusive. They are planning assumptions, not
confirmed cash transactions.

Select a historical planning time with an ISO 8601 timestamp containing a UTC
offset. The default is the current time:

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage rebalance \
  --as-of 2026-08-26T12:00:00Z
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
2. Build the required ticker set from the union of strategy positions and current
   holdings, including holdings that must be sold because they are absent from the
   strategy.
3. Refresh `interval=1d` data for the current year. Also refresh the previous year
   when it may contain the latest completed session, such as immediately after a
   year boundary.
4. Download tickers concurrently with a bounded worker pool. Write each successful
   yearly partition atomically and preserve existing successful partitions when
   another ticker or year fails.
5. Determine the expected latest completed trading session from an exchange
   calendar and the exchange timezone. Apply a provider-publication delay after
   the session close rather than assuming the final bar exists exactly at 16:00.
6. Validate that every required ticker has a coherent price, observation date,
   availability time, currency, provider, retrieval time, and source partition for
   that expected session.
7. Fail planning if any required observation is missing, conflicting, or stale.
   A future explicit `--allow-stale-prices` option may permit a reviewed exception;
   stale data must never be accepted implicitly.
8. Generate the plan only after the complete price set passes validation. Include
   the selected price provenance in every order as the current schema requires.

This workflow will use the downloader and rebalance planner as shared application
services rather than duplicating Yahoo Finance requests in the CLI dispatcher.

The command writes the JSON order plan to standard output, so it can be saved
for review or passed to a future broker adapter:

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage rebalance \
  --contribute 10000.00 > order-plan.json
```

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
