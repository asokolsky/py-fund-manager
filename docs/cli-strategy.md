# Strategy CLI

Strategy-assignment commands are an approved design and are not implemented yet.
The [storage contract](README.md#strategy-history-schema) defines their persisted
result, and [Concepts](concepts.md#strategy) defines assignment semantics.

## Show the effective strategy

```shell
mise run py-fund-manager -- \
  portfolio etrade-alex-roth-ira strategy show
```

By default, the command will show the assignment effective now. A future
`--effective-at` option will select the assignment governing another time.

## Show strategy history

```shell
mise run py-fund-manager -- \
  portfolio etrade-alex-roth-ira strategy history
```

The command will show assignments in effective-time order without modifying them.

## Assign a strategy

```shell
mise run py-fund-manager -- \
  portfolio etrade-alex-roth-ira strategy set SnP500-direct \
  --effective-at 2026-09-01T00:00:00Z \
  --reason "Move to direct S&P 500 replication"
```

The command will validate the strategy, calculate and preserve its immutable
revision, then append an assignment to `strategy-history.yaml` using an atomic
write. Omitting `--effective-at` will use the current time.

Assigning a strategy will not rebalance holdings, create financial transactions,
or submit broker orders. A separate rebalance command will use the assignment
effective at the requested planning time.
