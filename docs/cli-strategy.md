# Strategy CLI

The top-level `strategy analyze` command validates and summarizes a standalone
Strategy manifest. Strategy-assignment commands manage the effective strategy
for a portfolio. The
[schema reference](schemas.md#strategyhistory) defines their persisted
result, and [Concepts](concepts.md#strategy) defines assignment semantics.

## Analyze a strategy manifest

```shell
mise run py-fund-manager -- \
  strategy analyze sample-data/strategy/mag7/strategy.yaml
```

The command strictly parses and validates the manifest, then prints its name,
display name, benchmark, allocation type, position count, and total weight as
YAML. It does not require a configured data root and does not modify the file.

Use `--extract-tickers` to print sorted ticker symbols as a comma-separated
value accepted by the `download` command:

```shell
tickers=$(
  mise run py-fund-manager -- \
    strategy analyze sample-data/strategy/mag7/strategy.yaml \
    --extract-tickers
)
mise run py-fund-manager -- download 2026 --tickers="$tickers"
```

## Show the effective strategy

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage strategy show
```

By default, the command shows the assignment effective now. Use `--effective-at`
with an ISO 8601 timestamp containing a UTC offset to select the assignment
governing another time.

## Show strategy history

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage strategy history
```

The command shows assignments in effective-time order without modifying them.

## Assign a strategy

```shell
mise run py-fund-manager -- \
  portfolio etrade-brokerage strategy set SnP500-direct \
  --effective-at 2026-09-01T00:00:00Z \
  --reason "Move to direct S&P 500 replication"
```

The command validates the strategy, calculates and preserves its immutable
revision, then appends an assignment to `strategy-history.yaml` using an atomic
write. Omitting `--effective-at` uses the current time.

Assigning a strategy does not rebalance holdings, create financial transactions,
or submit broker orders. The rebalance command uses the assignment
effective at the requested planning time.
