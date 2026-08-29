# Playground Portfolio Regression

Playground is the fictional portfolio scenario protected by
[`tests/playground_test.py`](../../../tests/playground_test.py). It originated as
the acceptance scenario for deterministic historical broker execution: open an
account with USD 100,000, invest it in the equal-weight Magnificent Seven
strategy, process later account activity, and rebalance again.

This directory contains documentation only. The test creates the portfolio and
all private-style import files in a temporary data root on every run. Keeping the
generated ledger out of source control ensures the test exercises portfolio
creation, import, persistence, and reload behavior instead of relying on a
preassembled result.

## Inputs

The regression uses:

- The committed fictional [`mag7` strategy](../../strategy/mag7/README.md).
- An opening snapshot containing a USD 100,000 balance at
  `2020-01-02T16:00:00Z`.
- Deterministic USD 100 observations for every strategy security at each
  rebalance time.
- A simulated first rebalance on `2020-01-03T21:00:00Z`.
- A USD 70 dividend credited on `2020-03-13T16:00:00Z`.
- A second rebalance on `2020-03-13T21:00:00Z`.

The equal prices are deliberate test inputs, not reconstructed market history.
They isolate allocation, rounding, execution, persistence, and cash accounting
from price-provider behavior.

## Lifecycle under test

The regression performs this complete sequence:

1. Create the `playground` Portfolio manifest.
2. Import the agreed canonical `opening.csv` format.
3. Load the fictional equal-weight Mag7 strategy.
4. Generate the first rebalance plan from the opening balance.
5. Execute all seven orders through `HistoricalBroker`.
6. Convert and persist those confirmed executions through activity import.
7. Import the later dividend as a broker activity event.
8. Reload the ledger and verify that the dividend increased available cash.
9. Generate and execute a second seven-security rebalance.
10. Re-derive positions and verify that ending cash matches the plan and remains
    between zero and one cent.

This protects the boundary between opening snapshots, append-only account facts,
rebalance planning, the generic broker contract, deterministic historical fills,
and ledger-based state derivation.

## Scope

The regression does not download prices, read Parquet price partitions, contact a
broker, claim historical investment performance, model taxes or tax lots, or
handle stock splits. Those behaviors require separate fixtures and tests.

Run this scenario with:

```shell
mise exec -- uv run -m unittest -v tests.playground_test
```
