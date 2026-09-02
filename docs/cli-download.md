# Download CLI

The `download` command retrieves historical Yahoo Finance prices and writes the
validated partitions described by the [price-data
contract](../stocks-by-ticker/README.md).

## Help output

```shell
mise py-fund-manager -- download -h
```

```text
usage: py-fund-manager download [-h] --tickers TICKERS|@FILE
                                [--interval {1h,1d,1w}]
                                years

positional arguments:
  years                 Year or inclusive year range in YYYY or YYYY-YYYY form

options:
  -h, --help            show this help message and exit
  --tickers TICKERS|@FILE
                        Comma-separated ticker symbols or @ followed by a
                        ticker file
  --interval {1h,1d,1w}
                        Price-bar interval: 1h=hourly, 1d=daily, 1w=weekly
                        (default: 1d)
```

## Tickers and years

Supply a single year or an inclusive range and one or more comma-separated
tickers:

```shell
mise py-fund-manager -- download 2024-2025 --tickers=AAPL,MSFT
```

Use `@` to load one ticker per line from a UTF-8 file:

```shell
mise py-fund-manager -- download 2025 --tickers=@../pytickrs/tickers.txt
```

Ticker files may contain blank lines and comment lines beginning with `#`.
Symbols are normalized to uppercase, duplicates are removed, and download order
is unspecified. Relative ticker-file paths resolve from the current directory.

## Interval

The interval defaults to daily (`1d`). Hourly (`1h`) and weekly (`1w`) data are
also supported:

```shell
mise py-fund-manager -- download 2020 --tickers=MSFT --interval=1w
mise py-fund-manager -- download 2026 --tickers=AAPL --interval=1h
```

Yahoo Finance provides hourly history for approximately the most recent 730
days. Run `mise py-fund-manager -- download --help` for current options.
