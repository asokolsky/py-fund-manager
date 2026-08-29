# Playground Portfolio Regression

Playground is the fictional portfolio scenario covered by
[`tests/playground_test.py`](../../../tests/playground_test.py). It originated as
the acceptance scenario for deterministic historical broker execution:

- [Open an account with USD 100,000.](../../../tests/playground_test.py#L40-L52)
- [Invest it in the equal-weight Magnificent Seven strategy.](../../../tests/playground_test.py#L53-L87)
- [Process later account activity.](../../../tests/playground_test.py#L89-L99)
- [Rebalance again.](../../../tests/playground_test.py#L101-L132)

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

- [Create the `playground` Portfolio manifest.](../../../tests/playground_test.py#L40-L42)
- [Import the agreed canonical `opening.csv` format.](../../../tests/playground_test.py#L43-L52)
- [Load the fictional equal-weight Mag7 strategy.](../../../tests/playground_test.py#L53-L64)
- [Generate the first rebalance plan from the opening balance.](../../../tests/playground_test.py#L66-L78)
- [Execute all seven orders through `HistoricalBroker`.](../../../tests/playground_test.py#L79-L84)
- [Convert and persist those confirmed executions through activity import.](../../../tests/playground_test.py#L85-L87)
- [Import the later dividend as a broker activity event.](../../../tests/playground_test.py#L89-L95)
- [Reload the ledger and verify that the dividend increased available cash.](../../../tests/playground_test.py#L96-L99)
- [Generate and execute a second seven-security rebalance.](../../../tests/playground_test.py#L101-L116)
- [Re-derive positions and verify that ending cash matches the plan and remains
  between zero and one cent.](../../../tests/playground_test.py#L117-L132)

This covers the boundary between opening snapshots, append-only account facts,
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
