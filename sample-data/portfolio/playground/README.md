# Playground Portfolio

Playground is a fictional USD 100,000 portfolio used to exercise the same
commands and historical-price workflow available to a user. Historical prices
remain generated data: neither the Playground documentation nor the test suite
commits copies of the downloaded price partitions.

The commands below assume the configured data root contains the committed
[`mag7` strategy](../../strategy/mag7/README.md).

Although these commands are run now, the Playground portfolio is opened at the
historical effective time `2020-01-02T16:00:00Z`. January 2, 2020 was the first
U.S. trading day of that year, after the New Year's Day market holiday. Starting
with cash on that date gives the scenario a clear boundary before its first
rebalance on January 3. Every later plan, simulated fill, and activity event can
therefore use the committed timestamps and cached 2020 prices instead of the
current clock or current market data. This is a reproducible simulation
timeline, not a claim that a real account was opened in 2020.

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
  --as-of 2020-01-02T16:00:00Z
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
  fact effective at `2020-01-02T16:00:00Z`;
- `portfolio/playground/imports/playground-opening.csv`, an unchanged preserved
  copy of the import source.

The command creates these files at the time it is run; `--as-of` records when
the imported opening balance became effective in the simulated portfolio.

The regression verifies creation and opening import in
[`playground_test.py`](../../../tests/playground_test.py#L62-L70).

## 2. Assign the strategy

Make the equal-weight Magnificent Seven strategy effective from the opening
time:

```shell
mise run py-fund-manager -- \
  portfolio playground strategy set mag7 \
  --effective-at 2020-01-02T16:00:00Z \
  --reason "Open the Playground portfolio"
```

Expected outcome: the command prints the new assignment as YAML and creates
`sample-data/portfolio/playground/strategy-history.yaml`. The assignment refers
to the immutable `mag7` revision under
`sample-data/strategy/mag7/revisions/`; an already matching revision is reused,
not replaced. Portfolio holdings and cash remain unchanged.

The regression extracts the tickers from the same strategy manifest in
[`playground_test.py`](../../../tests/playground_test.py#L48-L49) and creates its
assignment in
[`playground_test.py`](../../../tests/playground_test.py#L72-L79).

## 3. Cache the historical prices

Isolate the strategy securities:

```sh
tickers=$(
  mise run py-fund-manager -- \
    strategy analyze sample-data/strategy/mag7/strategy.yaml --extract-tickers
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

The Playground regression uses those generated partitions directly and skips with a
download instruction when they are absent. It verifies that both planning and
execution load prices from that layout in
[`playground_test.py`](../../../tests/playground_test.py#L48-L102).
CLI download dispatch is covered in
[`main_test.py`](../../../tests/main_test.py#L70-L88).

## 4. Plan the first rebalance

Create the plan before the 2020-01-03 market close. At this time, the latest
eligible daily prices are the 2020-01-02 closes:

```shell
mise run py-fund-manager -- \
  portfolio playground \
  rebalance --as-of 2020-01-03T15:00:00Z \
  > rebalance-plan-2020-01-03.json
```

Expected outcome: `rebalance-plan-2020-01-03.json` is created in the current
directory. It is a strict JSON plan for `playground` containing the
2020-01-03T15:00:00Z valuation, the effective `mag7` assignment, price
provenance, and seven buy intents funded by the opening USD 100,000. Planning
does not update `transactions.csv` or place orders.

The regression verifies planning from the historical cache in
[`playground_test.py`](../../../tests/playground_test.py#L81-L95).

## 5. Create broker orders

A rebalance plan contains portfolio-level intents. A broker receives a normalized
order. Convert every intent into the `orders-2020-01-03.json` array:

```shell
jq '
  [
    .valuation as $valuation |
    .orders | to_entries[] |
    .key as $index |
    .value as $order |
    {
      id: (
        "playground-20200103T150000000000Z-" +
        ("0000" + (($index + 1) | tostring))[-4:]
      ),
      ticker: $order.ticker,
      side: $order.side,
      quantity: $order.quantity,
      currency: $valuation.currency,
      submitted_at: $valuation.as_of
    }
  ]
' rebalance-plan-2020-01-03.json > orders-2020-01-03.json
```

Expected outcome: `orders-2020-01-03.json` is created in the current directory
as an array of seven normalized orders in plan order. No portfolio or broker
state changes. The first element is this AAPL order:

```json
{
  "id": "playground-20200103T150000000000Z-0001",
  "ticker": "AAPL",
  "side": "buy",
  "quantity": "190.254033",
  "currency": "USD",
  "submitted_at": "2020-01-03T15:00:00Z"
}
```

The regression verifies the exact normalized order fields in
[`playground_test.py`](../../../tests/playground_test.py#L146-L152).

## 6. Fulfill the orders using historical prices

Submit the order array to the historical broker at the 2020-01-03 close:

```shell
mise run py-fund-manager -- \
  broker historical orders-2020-01-03.json --as-of 2020-01-03T21:00:00Z \
  > executions-2020-01-03.json
```

Expected outcome: `executions-2020-01-03.json` is created in the current
directory as an array containing one complete fill per order. The first fill has
ID `playground-20200103T150000000000Z-0001-fill-0001`, its execution time is
`2020-01-03T21:00:00Z`, and its price is the eligible AAPL close at that time.
These simulations still do not append fills to the portfolio ledger.

Planning used the AAPL close of `75.0875015258789` from 2020-01-02. The
historical broker independently loads the latest price available at execution
and fills the order at the different 2020-01-03 close of
`74.35749816894531`. The regression requires every first-rebalance execution
price to differ from its planning price in
[`playground_test.py`](../../../tests/playground_test.py#L153-L159). The
interactive command is covered in
[`main_test.py`](../../../tests/main_test.py#L90-L136).

## 7. Import confirmed executions

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
[`playground_test.py`](../../../tests/playground_test.py#L102-L108).

## 8. Import later account activity

Create an activity file for a USD 70 dividend:

```shell
printf '%s\n' \
  'occurred_at,event,asset,amount,external_id' \
  '2020-03-13T16:00:00Z,dividend,USD,70.00,playground-dividend-1' \
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
[`playground_test.py`](../../../tests/playground_test.py#L110-L120).

## 9. Rebalance again

Generate a second plan from the persisted positions and dividend-adjusted cash:

```shell
mise run py-fund-manager -- \
  portfolio playground rebalance --as-of 2020-03-13T21:00:00Z \
  > rebalance-plan-2020-03-13.json
```

Expected outcome: `rebalance-plan-2020-03-13.json` is created in the current
directory from the persisted post-fill positions and dividend-adjusted cash. It
contains seven Mag7 adjustment intents and the 2020-03-13T21:00:00Z price
provenance. Creating the plan does not change the ledger. After those orders are
fulfilled and their confirmed executions are imported as in steps 5-7, the
portfolio still holds all seven strategy securities and retains nonnegative
residual cash below one cent.

Create and fulfill each normalized order as above. The regression verifies the
second plan, historical fills, resulting positions, and nonnegative residual
cash in
[`playground_test.py`](../../../tests/playground_test.py#L122-L165).

## Run the regression coverage

Download the generated price cache, then run the Playground regression together
with its supporting portfolio, rebalance, and strategy suites:

```shell
mag7_tickers=$(
  mise run py-fund-manager -- \
    strategy analyze sample-data/strategy/mag7/strategy.yaml \
    --extract-tickers
)

mise run py-fund-manager -- \
  download 2020 \
  --tickers="$mag7_tickers"

mise exec -- uv run -m unittest -v \
  tests.main_test \
  tests.playground_test \
  tests.portfolio_test \
  tests.rebalance_test \
  tests.strategy_test
```

The scenario does not claim historical investment performance, model taxes or
tax lots, contact a live brokerage, or handle stock splits.
