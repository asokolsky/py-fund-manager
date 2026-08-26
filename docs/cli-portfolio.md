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

Prices come from the latest `interval=1d` close at or before `--as-of`. Missing
prices fail the plan; observations older than the planning date produce a warning.
Yahoo-style class-share symbols map strategy dots to price-cache hyphens, such as
`BRK.B` to `BRK-B`. All transaction and price currencies must match the portfolio's
base currency.

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
transactions. Non-closing quantities assume fractional-share trading and are
rounded down to six decimal places; notionals and target values are rounded to
cents. Broker-specific whole-share, minimum-order, tax-lot, and limit-price rules
remain outside this planner.

Run `mise run py-fund-manager -- portfolio --help` for current options.
