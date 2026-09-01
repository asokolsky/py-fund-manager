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
validates complete fills and resulting balances. Import the reviewed execution
JSON with the [portfolio command](cli-portfolio.md#import-broker-activity-or-executions).
