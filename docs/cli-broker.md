# Broker CLI

The `broker` command executes a reviewed rebalance plan and prints confirmed
fills as JSON. It does not append those fills to the portfolio ledger.

## Help output

```shell
mise run py-fund-manager -- broker -h
```

```text
usage: py-fund-manager broker [-h] {historical} ...

positional arguments:
  {historical}
    historical  Execute a plan from cached historical prices

options:
  -h, --help    show this help message and exit
```

The historical command identifies the reviewed plan and the explicit execution
time:

```shell
mise run py-fund-manager -- broker historical -h
```

```text
usage: py-fund-manager broker historical [-h] --as-of AS_OF plan_file

positional arguments:
  plan_file      Reviewed rebalance-plan JSON file

options:
  -h, --help     show this help message and exit
  --as-of AS_OF  Execution timestamp for cached historical prices
```

## Historical execution

Execute a plan against cached historical prices at an explicit time:

```shell
mise run py-fund-manager -- \
  broker historical rebalance-plan.json \
  --as-of 2026-08-26T14:00:00-07:00 \
  > executions-2026-08-26.json
```

The command reloads the portfolio and ledger named by the plan, verifies that
the plan still matches current state and the price-availability boundary, and
adapts each planned order to the historical broker's supported precision before
validating complete fills and resulting balances.

The historical broker defaults to E*TRADE-style share quantities: at most three
digits after the decimal point. It rounds planned quantities down to the nearest
`0.001` share so a buy cannot exceed its planned allocation. Sells round up to
fund their planned amount, capped at the available holding rounded down to the
supported increment; an exact
full-liquidation order preserves the remaining quantity even when it has more
than three decimal places. The library adapter accepts another nonnegative
`quantity_precision` when simulating a different broker; for example, `4` uses a
`0.0001`-share increment. A positive planned quantity or available holding that
rounds to zero is omitted. A sub-increment holding is therefore sellable only by
an exact full-liquidation order. The command reports an omission on standard
error while keeping execution JSON on standard output valid.

Execution prices are rounded half up to `0.01` for prices at or above `1.00` and
to `0.0001` for prices below `1.00`. Quantity rounding can leave more residual
cash than the six-decimal rebalance plan estimated. The simulation does not yet
model E*TRADE's eligible-security rules or minimum fractional-order notional.

Import the reviewed execution JSON with the [portfolio
command](cli-portfolio.md#import-broker-activity-or-executions).
