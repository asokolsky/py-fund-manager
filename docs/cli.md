# Command-Line Interface

Run the CLI through the repo's `mise` environment:

```shell
mise py-fund-manager -- COMMAND [OPTIONS]
```

Available commands:

- [`validate`](cli-validate.md) verifies every discovered manifest, ledger,
  Strategy reference, and immutable revision without writes or network access.
- [`portfolio`](cli-portfolio.md) creates portfolio metadata and imports opening
  snapshots and ongoing broker activity, and browses effective account state in
  an interactive terminal.
- [`download`](cli-download.md) downloads historical market prices.
- [`broker`](cli-broker.md) executes reviewed rebalance plans and prints
  confirmed fills.
- [`strategy`](cli-strategy.md) validates and inspects standalone Strategy
  manifests; portfolio strategy operations inspect and change effective
  assignments.

Use `mise py-fund-manager -- --help` for the current command list.

See the [validate command guide](cli-validate.md) for complete data-root checks,
output, exit statuses, and side-effect guarantees.

Portfolio commands use the per-user [data-root
configuration](data.md#data-root-configuration).

Show the installed version:

```shell
mise py-fund-manager -- --version
```

## Help output

```shell
mise py-fund-manager -- -h
```

```text
usage: py-fund-manager [-h] [-v] [--version]
                       {validate,download,broker,strategy,portfolio} ...

py-fund-manager cli v0.2.1

positional arguments:
  {validate,download,broker,strategy,portfolio}
    validate            Validate the complete configured data root
    download            Download historical stock prices
    broker              Execute rebalance plans
    strategy            Inspect standalone strategy manifests
    portfolio           Create and manage portfolios

options:
  -h, --help            show this help message and exit
  -v, --verbose         Tell more about what is going on
  --version             Display module version and exit.

Examples:
    python -m py_fund_manager --version
    python -m py_fund_manager -v download 2024-2025 --tickers=AAPL,MSFT
    python -m py_fund_manager download 2020 --tickers=@tickers.txt --interval=1w
    python -m py_fund_manager strategy show strategy.yaml
    python -m py_fund_manager strategy tickers strategy.yaml
    python -m py_fund_manager portfolio create brokerage --broker historical --account-id 1234
    python -m py_fund_manager portfolio create playground --broker historical --account-id playground --as-of 2020-01-02T08:00:00-08:00 --balance=USD:10000,AMAT:22
    python -m py_fund_manager portfolio create brokerage --broker historical --account-id 1234 --balance=@opening.csv
    python -m py_fund_manager portfolio import brokerage activity.csv
    python -m py_fund_manager portfolio browse
    python -m py_fund_manager portfolio strategy brokerage show
```
