# stocks-by-ticker README

This directory stores hourly, daily, or weekly historical stock prices. Each
interval and ticker has its own directory, with its history split into one
Parquet file per calendar year.

This market-price cache is independent of portfolio account data. Portfolio
metadata and transaction ledgers follow [the portfolio storage contract](../docs/README.md);
they must not be placed in this generated directory.

Use this layout when analysis focuses on one stock at a time, such as training a
model per ticker or calculating technical indicators like MACD and RSI. A layout
partitioned by date first is more efficient for portfolio-wide queries across
many tickers.

## Directory layout

```text
stocks-by-ticker/
├── interval=1h/
│   └── ticker=AAPL/
│       └── year=2025/data.parquet
├── interval=1d/
│   └── ticker=AAPL/
│       ├── year=2024/data.parquet
│       └── year=2025/data.parquet
└── interval=1w/
    └── ticker=MSFT/
        └── year=2020/data.parquet
```

The `interval`, `ticker`, and `year` directory names use Hive-style partitioning.
Ticker symbols must be normalized to uppercase.

## Creating files

The downloader creates interval, ticker, and year directories automatically; do
not create or edit Parquet files by hand. See the [download CLI
guide](../docs/cli-download.md) for command syntax, ticker-file input, intervals,
and examples.

The downloader processes as many as six tickers concurrently. Each successful
result is written atomically to
`interval=INTERVAL/ticker=SYMBOL/year=YYYY/data.parquet`. Running the same request
again replaces the affected yearly files. Each year is requested independently;
a failed year does not prevent later years from being attempted. A failure for
one ticker is reported without removing files successfully written for other
tickers.

## Granularity and trading sessions

For `interval=1h`, each row represents an hourly bar and uses an exchange-local,
timezone-aware `timestamp`. For `interval=1d`, each row represents one daily bar
for one exchange trading session. For `interval=1w`, each row represents one
weekly bar aggregated by the data provider. Daily and weekly bars use the
provider's exchange-local `date`. Only regular-session prices are included;
pre-market and after-hours trading are excluded. Missing bars must not be
forward-filled in stored data.

## Schema

Each Parquet file uses the following columns:

Hourly files contain `timestamp`; daily and weekly files contain `date`. These
two time columns are mutually exclusive.

| Column | Type | Description |
| --- | --- | --- |
| `date` | date | Exchange-local trading date for daily and weekly bars |
| `timestamp` | timestamp | Exchange-local, timezone-aware time for hourly bars |
| `open` | float64 | Unadjusted regular-session opening price |
| `high` | float64 | Unadjusted regular-session high price |
| `low` | float64 | Unadjusted regular-session low price |
| `close` | float64 | Unadjusted regular-session closing price |
| `adjusted_close` | float64 | Closing price adjusted for splits and dividends |
| `volume` | int64 | Reported regular-session volume |
| `dividends` | float64 | Cash dividend attributed to the trading date, or zero |
| `stock_splits` | float64 | Split ratio attributed to the trading date, or zero |

Interval, ticker, and year are partition values encoded in the directory path
and do not need to be duplicated in each Parquet row.

## Data rules

- Each `(ticker, interval, date)` or `(ticker, interval, timestamp)` tuple must be
  unique.
- Rows must be sorted by `date` or `timestamp` in ascending order.
- OHLC values remain unadjusted. Use `adjusted_close` when calculating total
  historical returns across dividends or stock splits.
- Missing observations remain missing; stored prices must not be interpolated or
  forward-filled.
- Dataset metadata or an accompanying manifest must record the data source,
  retrieval time in UTC, exchange time zone, trading currency, and bar interval
  (`1h`, `1d`, or `1w`).
- Rebalance planning treats a daily close as available at 16:00 in the recorded
  exchange timezone. The price, date, currency, source, and partition remain one
  observation; metadata from another partition cannot qualify the selected row.
- Refreshes replace the affected yearly file atomically after validating its
  schema, uniqueness, ordering, and partition year.
