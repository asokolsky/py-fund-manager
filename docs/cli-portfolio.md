# Portfolio CLI

The `portfolio` command creates validated portfolio metadata and can bootstrap
opening positions from canonical holdings. See the [storage
contract](README.md) for schemas and validation rules.

## Create a portfolio

```shell
mise run py-fund-manager -- portfolio --create etrade-alex-roth-ira
```

The command creates `portfolios/etrade-alex-roth-ira/portfolio.yaml` below the
root selected by the required [global configuration](cli.md#data-root).

## Import opening positions

Bootstrap a new portfolio from canonical holdings during creation:

```shell
mise run py-fund-manager -- \
  portfolio --create etrade-alex-roth-ira \
  import-stocks /path/to/private/stocks.csv
```

The command validates and preserves the source CSV, then writes one
`opening_position` transaction per holding. Existing imports and transaction
ledgers are not replaced.

Run `mise run py-fund-manager -- portfolio --help` for current options.
