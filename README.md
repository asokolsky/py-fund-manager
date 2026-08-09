# py-fund-manager

## Terminology

portfolio is a set of tickers.

fund is a portfolio with a pre-defined algorithm of splitting inflows between tickers.

## Funds

Directory structure: directory per fund.

## Download historical prices

Download daily historical prices for one or more comma-separated tickers and an
inclusive year range:

```shell
mise run py-fund-manager -- download 2024-2025 --tickers=AAPL,MSFT
mise run py-fund-manager -- download 2020 --tickers=MSFT --interval=1w
mise run py-fund-manager -- download 2020 --tickers=@../pytickrs/tickers.txt
```

`--tickers` accepts either comma-separated symbols or `@` followed by a UTF-8
file containing one ticker per line. Blank lines and lines beginning with `#`
are ignored. Duplicate symbols are removed, and download order is unspecified.
The year may be a single year or an inclusive range. The interval defaults to
daily (`1d`); hourly and weekly bars are selected with `--interval=1h` and
`--interval=1w`, respectively. Yahoo Finance limits how far back intraday data
can be requested.

The command writes one Parquet file per interval, ticker, and year under
`stocks-by-ticker/interval=INTERVAL/ticker=SYMBOL/year=YYYY/data.parquet`.
Existing yearly files are replaced atomically after a successful download and
validation. Multiple tickers are downloaded concurrently using up to six
workers. Each year is requested independently, so a failed year does not prevent
later years from being attempted. Failures are reported per ticker without
discarding successful files.

See [stocks-by-ticker/README.md](stocks-by-ticker/README.md) for the directory
layout, Parquet schema, data rules, and additional download examples.

## Tests

Run the unit tests from the repository root:

```shell
mise run tests
```

Run all local quality checks before committing:

```shell
mise run format
mise run lint
mise run mypy
mise run tests
```

## Sources

- [Portfolio Management Of Multiple Strategies Using Python](https://blog.quantinsti.com/portfolio-management-strategy-python/)
- [inverno](https://github.com/werew/inverno)
- [how-to-construct-portfolio-to-track-broad-market](https://medium.com/@pingzhang0108/how-to-construct-portfolio-to-track-broad-market-with-python-8900fcb6541a), [Integer-Programming-for-Portfolio-Construction](https://github.com/ping2022/Integer-Programming-for-Portfolio-Construction)
