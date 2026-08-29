# Playground Portfolio

Playground is a fictional USD 100,000 portfolio used to exercise the same
commands and historical-price workflow available to a user. Historical prices
remain generated data: neither the Playground documentation nor the test suite
commits copies of the downloaded price partitions.

The commands below assume the configured data root contains the committed
[`mag7` strategy](../../strategy/mag7/README.md).

Although these commands are run now, the Playground portfolio is opened at the
historical effective time `2020-01-02T08:00:00-08:00` in Pacific Standard Time
(PST). January 2, 2020 was the first U.S. trading day of that year, after the New
Year's Day market holiday. Starting with cash on that date gives the scenario a
clear boundary before its first rebalance on January 3. Every later plan,
simulated fill, and activity event can therefore use the committed timestamps
and cached 2020 prices instead of the current clock or current market data. The
The March, June, and September timestamps use Pacific Daylight Time (PDT), with
offset `-07:00`. This is a reproducible simulation timeline, not a claim that a
real account was opened in the year 2020.

## Timeline

Every financial event, plan, and execution uses an explicit Pacific timestamp:

| Step | Simulated time | Expected boundary |
| --- | --- | --- |
| 0 | Current local time | Remove only generated Playground state from an earlier run. |
| 1 | 2020-01-02 08:00 PST | Open the portfolio with USD 100,000 cash. |
| 2 | 2020-01-02 08:00 PST | Make `mag7` the effective strategy from opening. |
| 3 | 2020 price-history range | Cache daily prices from 2020-01-01 through 2020-12-31; no portfolio state changes. |
| 4 | 2020-01-03 07:00 PST | Plan from the 2020-01-02 closing prices. |
| 5 | 2020-01-03 13:00 PST | Execute the first plan at the 2020-01-03 close. |
| 6 | After step 5 | Import fills timestamped 2020-01-03 13:00 PST. |
| 7 | 2020-03-13 09:00 PDT | Import a confirmed USD 70 dividend. |
| 8 | 2020-03-13 14:00 PDT | Plan, execute, and import the dividend rebalance. |
| 9 | 2020-06-15 09:00 and 14:00 PDT | Import USD 5,000, then plan and execute its rebalance. |
| 10 | After step 9 | Convert and import fills timestamped 2020-06-15 14:00 PDT. |
| 11 | 2020-09-01 14:00 PDT | Plan a USD 1,000 withdrawal and generate the required sell orders. |
| 12 | 2020-09-02 14:00 PDT | Fulfill the sell orders at the next trading day's historical close. |
| 13 | After step 12 | Convert and import fills timestamped 2020-09-02 14:00 PDT. |
| 14 | 2020-09-03 09:00 PDT | Record the confirmed USD 1,000 transfer to an outside account. |

Steps 1-6 use PST (`-08:00`). Daylight saving time is in effect for steps 7-14,
so those timestamps use PDT (`-07:00`). Planning and execution in steps 8 and 9
share a timestamp because the historical broker fills at the same eligible close
used by each plan. Steps 6 and 10 happen after execution, but import fills with
the execution timestamp rather than the wall-clock import time. Step 12 instead
uses the next trading day's close to demonstrate that a withdrawal plan can be
fulfilled after prices move.

## 0. Reset an earlier Playground run

Portfolio creation preserves this tracked README but refuses to overwrite data
from an earlier run. Preview the ignored generated files that would be removed:

```shell
git clean -ndX -- sample-data/portfolio/playground/
```

After verifying every listed path belongs to the disposable Playground run,
remove those files:

```shell
git clean -fdX -- sample-data/portfolio/playground/
```

This reset is intentionally explicit and scoped to the Playground portfolio. It
does not remove the shared historical-price cache or output files such as
`rebalance-plan-2020-01-03.json` created elsewhere.

## 1. Create the playground portfolio

Create the portfolio and import the committed
[`playground-opening.csv`](../../../tests/data/playground-opening.csv) snapshot:

```sh
mise run py-fund-manager -- \
  portfolio --create playground import tests/data/playground-opening.csv \
  --as-of 2020-01-02T08:00:00-08:00
```

The opening CSV contains:

```csv
asset,quantity,amount,cost_basis
USD,,100000.00,
```

Expected outcome: the command reports that it created `playground` and imported
one opening fact. Relative to the configured `sample-data/` root, it preserves
this README and creates:

- `portfolio/playground/portfolio.yaml` with `metadata.name: playground`, a USD
  base currency, and the Playground account identity;
- `portfolio/playground/transactions.csv` with the USD 100,000 opening-cash
  fact effective at `2020-01-02T08:00:00-08:00`;
- `portfolio/playground/imports/playground-opening.csv`, an unchanged preserved
  copy of the import source.

The command creates these files at the time it is run; `--as-of` records when
the imported opening balance became effective in the simulated portfolio.

The regression verifies creation and opening import in
[`playground_test.py`](../../../tests/playground_test.py).

## 2. Assign the strategy

Make the equal-weight Magnificent Seven strategy effective from the opening
time:

```shell
mise run py-fund-manager -- \
  portfolio playground strategy set mag7 \
  --as-of 2020-01-02T08:00:00-08:00 \
  --reason "Open the Playground portfolio"
```

Expected outcome: the command prints the new assignment as YAML and creates
`sample-data/portfolio/playground/strategy-history.yaml`. The assignment refers
to the immutable `mag7` revision under
`sample-data/strategy/mag7/revisions/`; an already matching revision is reused,
not replaced. Portfolio holdings and cash remain unchanged.

The regression extracts the tickers from the same strategy manifest in
[`playground_test.py`](../../../tests/playground_test.py) and creates its
assignment in
[`playground_test.py`](../../../tests/playground_test.py).

## 3. Cache the historical prices

Isolate the strategy securities:

```sh
tickers=$(
  mise run py-fund-manager -- \
    strategy tickers sample-data/strategy/mag7/strategy.yaml
)
```

Download daily 2020 prices for every security in the strategy:

```sh
mise run py-fund-manager -- download 2020 --tickers="$tickers"
```

Expected outcome: `tickers` contains
`AAPL,AMZN,GOOGL,META,MSFT,NVDA,TSLA`. The download creates or refreshes one
generated file per ticker at
`stocks-by-ticker/interval=1d/ticker=TICKER/year=2020/data.parquet` and reports
each file it writes. It does not create or change a portfolio.

The Playground regression creates equivalent deterministic Parquet partitions in
a temporary directory. It exercises the same price-loading path without depending
on downloaded files or skipping on a fresh clone. See
[`playground_test.py`](../../../tests/playground_test.py).
CLI download dispatch is covered in
[`main_test.py`](../../../tests/main_test.py).

## 4. Plan the first rebalance

Create the plan before the 2020-01-03 market close. At this time, the latest
eligible daily prices are the 2020-01-02 closes:

```shell
mise run py-fund-manager -- \
  portfolio playground \
  rebalance --as-of 2020-01-03T07:00:00-08:00 \
  > rebalance-plan-2020-01-03.json
```

Expected outcome: `rebalance-plan-2020-01-03.json` is created in the current
directory. It is a strict JSON plan for `playground` containing the
2020-01-03T07:00:00-08:00 valuation, the effective `mag7` assignment, price
provenance, and seven buy intents funded by the opening USD 100,000. Planning
does not update `transactions.csv` or place orders.

The regression verifies planning from the historical cache in
[`playground_test.py`](../../../tests/playground_test.py).

## 5. Fulfill the rebalancing plan

Submit the complete plan to the historical broker at the 2020-01-03 close:

```shell
mise run py-fund-manager -- \
  broker historical rebalance-plan-2020-01-03.json \
  --as-of 2020-01-03T13:00:00-08:00 \
  > executions-2020-01-03.json
```

Expected outcome: `executions-2020-01-03.json` is created in the current
directory as an array containing one complete fill per order. The first fill has
ID `playground-20200103T150000000000Z-0001-fill-0001`, its execution time is
`2020-01-03T13:00:00-08:00`, and its price is the eligible AAPL close at that
time. The command loads the portfolio and its transaction ledger, confirms that
the plan still matches that ledger, validates every fill, and rejects an
execution that would derive negative cash or incorrect positions. It prints
confirmed executions but does not append them to the ledger.

Planning used the AAPL close of `75.0875015258789` from 2020-01-02. The
historical broker independently loads the latest price available at execution
and fills the order at the different 2020-01-03 close of
`74.35749816894531`. The regression requires every first-rebalance execution
price to differ from its planning price in
[`playground_test.py`](../../../tests/playground_test.py). The
interactive command is covered in
[`main_test.py`](../../../tests/main_test.py).

## 6. Import confirmed executions

Convert the seven confirmed fills into the canonical activity CSV:

```shell
jq -r '
  (
    ["occurred_at", "event", "asset", "quantity", "price", "fees", "external_id"],
    (
      .[] |
      [
        .executed_at,
        .side,
        .ticker,
        .quantity,
        .price,
        (.fees // "0"),
        .id
      ]
    )
  ) |
  @csv
' executions-2020-01-03.json > rebalance-2020-01-03.csv
```

Import that CSV:

```sh
mise run py-fund-manager -- portfolio playground import rebalance-2020-01-03.csv
```

Expected outcome: `rebalance-2020-01-03.csv` contains a header and seven rows in
execution order. The import command reports seven imported activity events,
preserves the source as
`sample-data/portfolio/playground/imports/rebalance-2020-01-03.csv`, and appends
seven buy facts with confirmed execution prices to
`sample-data/portfolio/playground/transactions.csv`. Re-importing an identical
file skips the known events instead of duplicating them.

The activity file contains one `buy` or `sell` row per confirmed execution, not
the estimated prices from `rebalance-plan-2020-01-03.json`. The regression writes
and imports all seven confirmed fills in
[`playground_test.py`](../../../tests/playground_test.py).

## 7. Import later account activity

The following command shows how the dividend activity fixture is generated:

```shell
printf '%s\n' \
  'occurred_at,event,asset,amount,external_id' \
  '2020-03-13T09:00:00-07:00,dividend,USD,70.00,playground-dividend-1' \
  > tests/data/activity-2020-03-13.csv
```

That exact file is already committed as
[`activity-2020-03-13.csv`](../../../tests/data/activity-2020-03-13.csv), so the
generation command does not need to be run. Import the committed fixture:

```shell
mise run py-fund-manager -- \
  portfolio playground import tests/data/activity-2020-03-13.csv
```

Expected outcome: the command imports one event from the committed
[`activity-2020-03-13.csv`](../../../tests/data/activity-2020-03-13.csv),
preserves a copy at
`sample-data/portfolio/playground/imports/activity-2020-03-13.csv`, and appends
one USD 70 dividend fact to
`sample-data/portfolio/playground/transactions.csv`. Derived cash increases by
exactly USD 70; security quantities do not change.

The regression verifies the import and the USD 70 cash increase in
[`playground_test.py`](../../../tests/playground_test.py).

## 8. Rebalance: Create and Execute the Rebalancing Plan

Generate a second plan from the persisted positions and dividend-adjusted cash:

```shell
mise run py-fund-manager -- \
  portfolio playground rebalance --as-of 2020-03-13T14:00:00-07:00 \
  > rebalance-plan-2020-03-13.json
```

Expected outcome: `rebalance-plan-2020-03-13.json` is created in the current
directory from the persisted post-fill positions and dividend-adjusted cash. It
contains seven Mag7 adjustment intents and the 2020-03-13T14:00:00-07:00 price
provenance. Creating the plan does not change the ledger.

Execute the second plan at the same eligible close:

```shell
mise run py-fund-manager -- broker historical rebalance-plan-2020-03-13.json \
  --as-of 2020-03-13T14:00:00-07:00 \
  > executions-2020-03-13.json
```

Convert and import the confirmed fills:

```shell
jq -r '
  (
    ["occurred_at", "event", "asset", "quantity", "price", "fees", "external_id"],
    (.[] | [.executed_at, .side, .ticker, .quantity, .price, (.fees // "0"), .id])
  ) |
  @csv
' executions-2020-03-13.json > rebalance-2020-03-13.csv

mise run py-fund-manager -- portfolio playground import rebalance-2020-03-13.csv
```

Expected outcome: `executions-2020-03-13.json` and
`rebalance-2020-03-13.csv` are created in the current directory. The import
preserves `portfolio/playground/imports/rebalance-2020-03-13.csv` and appends
seven confirmed fills to `portfolio/playground/transactions.csv`. The portfolio
still holds all seven strategy securities and retains nonnegative residual cash
below one cent.

The regression verifies the second plan, imports all historical fills, and checks
the resulting positions and cash in
[`playground_test.py`](../../../tests/playground_test.py).

## 9. Contribute USD 5,000 and rebalance

The following command shows how the contribution activity fixture is generated:

```shell
printf '%s\n' \
  'occurred_at,event,asset,amount,external_id' \
  '2020-06-15T09:00:00-07:00,deposit,USD,5000.00,playground-contribution-1' \
  > tests/data/activity-2020-06-15.csv
```

That exact file is already committed as
[`activity-2020-06-15.csv`](../../../tests/data/activity-2020-06-15.csv), so the
generation command does not need to be run. Import the committed fixture:

```shell
mise run py-fund-manager -- \
  portfolio playground import tests/data/activity-2020-06-15.csv
```

Expected outcome: the command imports one event from the committed
[`activity-2020-06-15.csv`](../../../tests/data/activity-2020-06-15.csv),
preserves it as `portfolio/playground/imports/activity-2020-06-15.csv`, and
appends one USD 5,000 deposit to `portfolio/playground/transactions.csv`.
Available cash increases by exactly USD 5,000. This uses a confirmed ledger
event, not the unconfirmed `rebalance --contribute` planning assumption.

Generate the contribution rebalance at the 2020-06-15 close:

```shell
mise run py-fund-manager -- \
  portfolio playground rebalance --as-of 2020-06-15T14:00:00-07:00 \
  > rebalance-plan-2020-06-15.json
```

Expected outcome: `rebalance-plan-2020-06-15.json` is created from the persisted
post-dividend positions and contribution-adjusted cash. It contains seven Mag7
adjustment intents and does not modify the ledger.

Execute the plan at the same eligible close:

```shell
mise run py-fund-manager -- \
  broker historical rebalance-plan-2020-06-15.json \
  --as-of 2020-06-15T14:00:00-07:00 \
  > executions-2020-06-15.json
```

Expected outcome: `executions-2020-06-15.json` is created in the current
directory with seven confirmed fills timestamped
`2020-06-15T14:00:00-07:00`. Execution does not modify
`portfolio/playground/transactions.csv`; the fills remain external results until
step 10 imports them.

## 10. Import the contribution-rebalance executions

Convert and import the confirmed fills:

```shell
jq -r '
  (
    ["occurred_at", "event", "asset", "quantity", "price", "fees", "external_id"],
    (.[] | [.executed_at, .side, .ticker, .quantity, .price, (.fees // "0"), .id])
  ) |
  @csv
' executions-2020-06-15.json > rebalance-2020-06-15.csv

mise run py-fund-manager -- portfolio playground import rebalance-2020-06-15.csv
```

Expected outcome: `rebalance-2020-06-15.csv` is created in the current directory
from `executions-2020-06-15.json`. The import preserves
`portfolio/playground/imports/rebalance-2020-06-15.csv` and appends seven
confirmed fills to `portfolio/playground/transactions.csv`. The final portfolio
holds all seven strategy securities and retains nonnegative residual cash below
one cent.

The regression imports the USD 5,000 contribution, verifies the exact cash
increase, generates and executes the third plan without changing the ledger,
then imports all seven fills and checks the final positions and residual cash in
[`playground_test.py`](../../../tests/playground_test.py).

## 11. Plan a USD 1,000 withdrawal

Generate a plan that reserves USD 1,000 for transfer out of the portfolio:

```shell
mise run py-fund-manager -- \
  portfolio playground rebalance \
  --as-of 2020-09-01T14:00:00-07:00 \
  --withdraw 1000.00 \
  > rebalance-plan-2020-09-01.json
```

Expected outcome: `rebalance-plan-2020-09-01.json` is created in the current
directory. Its valuation records a USD 1,000 planned withdrawal, reduces the
target portfolio value by that amount, and contains sell orders that raise the
reserved cash while keeping the remaining holdings aligned with `mag7`.
Planning neither changes the ledger nor moves money.

## 12. Fulfill the withdrawal orders the next day

Execute the reviewed plan at the next trading day's historical close:

```shell
mise run py-fund-manager -- \
  broker historical rebalance-plan-2020-09-01.json \
  --as-of 2020-09-02T14:00:00-07:00 \
  > executions-2020-09-02.json
```

Expected outcome: `executions-2020-09-02.json` is created in the current
directory with complete sell fills priced from the 2020-09-02 historical close.
The broker rejects the execution if those actual fills would leave less than USD
1,000 cash for the planned withdrawal. Successful execution prints confirmed
fills but does not modify `portfolio/playground/transactions.csv`.

## 13. Import the withdrawal-funding executions

Convert the confirmed fills and import them into the portfolio ledger:

```shell
jq -r '
  (
    ["occurred_at", "event", "asset", "quantity", "price", "fees", "external_id"],
    (.[] | [.executed_at, .side, .ticker, .quantity, .price, (.fees // "0"), .id])
  ) |
  @csv
' executions-2020-09-02.json > rebalance-2020-09-02.csv

mise run py-fund-manager -- portfolio playground import rebalance-2020-09-02.csv
```

Expected outcome: `rebalance-2020-09-02.csv` is created in the current directory,
preserved as `portfolio/playground/imports/rebalance-2020-09-02.csv`, and its
confirmed sells are appended to `portfolio/playground/transactions.csv`. Derived
cash is now at least USD 1,000, but no withdrawal has occurred yet.

## 14. Transfer USD 1,000 to an outside account

The following command shows how the withdrawal activity fixture is generated:

```shell
printf '%s\n' \
  'occurred_at,event,asset,amount,external_id' \
  '2020-09-03T09:00:00-07:00,withdrawal,USD,1000.00,playground-outside-account-1' \
  > tests/data/activity-2020-09-03.csv
```

That exact file is already committed as
[`activity-2020-09-03.csv`](../../../tests/data/activity-2020-09-03.csv), so the
generation command does not need to be run. Import the committed fixture:

```shell
mise run py-fund-manager -- \
  portfolio playground import tests/data/activity-2020-09-03.csv
```

Expected outcome: the command imports one event from the committed
[`activity-2020-09-03.csv`](../../../tests/data/activity-2020-09-03.csv),
preserves it as `portfolio/playground/imports/activity-2020-09-03.csv`, and
appends one confirmed USD 1,000 withdrawal to
`portfolio/playground/transactions.csv`. Portfolio cash decreases by exactly USD
1,000 without changing security quantities. The outside account is represented
by the external transaction ID; its own balance is outside this portfolio's
ledger.

The regression generates the withdrawal plan, fulfills it from the next day's
prices, verifies the execution reserves at least USD 1,000, imports the fills,
then imports the withdrawal and verifies the exact cash movement in
[`playground_test.py`](../../../tests/playground_test.py).

## Run the regression coverage

Run the hermetic Playground regression together with its supporting portfolio,
rebalance, and strategy suites:

```shell
mise exec -- uv run -m unittest -v \
  tests.main_test \
  tests.playground_test \
  tests.portfolio_test \
  tests.rebalance_test \
  tests.strategy_test
```

The scenario does not claim historical investment performance, model taxes or
tax lots, contact a live brokerage, or handle stock splits.
