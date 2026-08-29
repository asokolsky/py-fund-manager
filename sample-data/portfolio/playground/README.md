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
March timestamps use Pacific Daylight Time (PDT), with offset `-07:00`. This is a
reproducible simulation timeline, not a claim that a real account was opened in
the year 2020.

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

## 5. Fulfill the plan using historical prices

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

Create an activity file for a USD 70 dividend:

```shell
printf '%s\n' \
  'occurred_at,event,asset,amount,external_id' \
  '2020-03-13T09:00:00-07:00,dividend,USD,70.00,playground-dividend-1' \
  > activity-2020-03-13.csv
```

Import it:

```shell
mise run py-fund-manager -- portfolio playground import activity-2020-03-13.csv
```

Expected outcome: `activity-2020-03-13.csv` is first created in the current
directory. Importing it reports one imported activity event, preserves a copy
at `sample-data/portfolio/playground/imports/activity-2020-03-13.csv`, and
appends one USD 70 dividend fact to
`sample-data/portfolio/playground/transactions.csv`. Derived cash increases by
exactly USD 70; security quantities do not change.

The regression verifies the import and the USD 70 cash increase in
[`playground_test.py`](../../../tests/playground_test.py).

## 8. Rebalance again

Generate a second plan from the persisted positions and dividend-adjusted cash:

```shell
mise run py-fund-manager -- \
  portfolio playground rebalance --as-of 2020-03-13T14:00:00-07:00 \
  > rebalance-plan-2020-03-13.json
```

Expected outcome: `rebalance-plan-2020-03-13.json` is created in the current
directory from the persisted post-fill positions and dividend-adjusted cash. It
contains seven Mag7 adjustment intents and the 2020-03-13T14:00:00-07:00 price
provenance. Creating the plan does not change the ledger. After the plan is
fulfilled and its confirmed executions are imported as in steps 5-6, the
portfolio still holds all seven strategy securities and retains nonnegative
residual cash below one cent.

Fulfill the plan and import its executions as above. The regression verifies the
second plan, historical fills, resulting positions, and nonnegative residual
cash in [`playground_test.py`](../../../tests/playground_test.py).

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
