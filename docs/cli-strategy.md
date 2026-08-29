# Strategy CLI

The top-level `strategy show` and `strategy tickers` commands inspect a standalone
Strategy manifest. Strategy-assignment commands manage the effective strategy
for a portfolio. The
[schema reference](schemas.md#strategyhistory) defines their persisted
result, and [Concepts](concepts.md#strategy) defines assignment semantics.

## Show a strategy manifest

```shell
mise run py-fund-manager -- \
  strategy show sample-data/strategy/mag7/strategy.yaml
```

The command strictly parses and validates the manifest, then prints its name,
display name, benchmark, allocation type, position count, and total weight as
YAML. It does not require a configured data root and does not modify the file.

Use `strategy tickers` to print sorted ticker symbols as a comma-separated value
accepted by the `download` command:

```shell
tickers=$(
  mise run py-fund-manager -- \
    strategy tickers sample-data/strategy/mag7/strategy.yaml
)
mise run py-fund-manager -- download 2026 --tickers="$tickers"
```

## Show the effective strategy

```shell
mise run py-fund-manager -- \
  portfolio strategy brokerage show
```

By default, the command shows the assignment effective now. Use `--as-of` with an
ISO 8601 timestamp containing a timezone offset to select the assignment governing
another time.

## Show strategy history

```shell
mise run py-fund-manager -- \
  portfolio strategy brokerage history
```

The command shows assignments in effective-time order without modifying them.

## Assign a strategy

```shell
mise run py-fund-manager -- \
  portfolio strategy brokerage set SnP500-direct \
  --as-of 2026-09-01T09:00:00-07:00 \
  --reason "Move to direct S&P 500 replication"
```

The command validates the strategy, calculates and preserves its immutable
revision, then appends an assignment to `strategy-history.yaml` using an atomic
write. Omitting `--as-of` uses the current time.

Assigning a strategy does not rebalance holdings, create financial transactions,
or submit broker orders. The rebalance command uses the assignment
effective at the requested planning time.
