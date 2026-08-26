# Design Decisions

This document records settled storage and validation choices. See
[Concepts](concepts.md) for the domain model and [Portfolio Storage and
Validation](README.md) for the resulting contract.

## Separate portfolio, ledger, import, and strategy files

Portfolio metadata changes rarely, the transaction ledger grows over time, raw
imports are audit evidence, and strategies may be shareable. A single file would
mix different schemas, update patterns, and privacy boundaries. Each concern
therefore has its own file and directory.

Transactions are persisted as rows in an ordered CSV ledger, with a stable ID per
row. Date and ticker indexes are derived in memory rather than encoded as a
persisted dictionary.

## File formats follow the data lifecycle

YAML is used for small, human-readable portfolio and strategy configuration. CSV
is used for append-oriented transactions and broker interchange. Parquet is used
only for generated historical-price data, where typed columns and analytical
reads matter more than manual editing. Generated Parquet files are never account
or transaction sources of truth.

## Decimal values preserve financial precision

Quantities, prices, amounts, fees, cost basis, and strategy weights use Python
`Decimal`, not binary floating-point values. Strategy weights are persisted as
quoted decimal fractions. They must be nonnegative and total `1.0` within a
`0.000001` tolerance. This retains more precision than integer thousandths or
basis points for small positions.

## Pydantic models are the schema authority

`Portfolio`, `Transaction`, `Strategy`, and `TargetAllocation` in
`py_fund_manager/portfolio.py` are frozen Pydantic models. Persisted-document
loaders reject unknown fields, unsupported YAML schema versions, invalid
identifiers and tickers, naive timestamps, invalid decimal values, and
transaction shapes missing required security fields. Examples in the storage
contract describe those models; they are not independent schemas.

## Private account data stays outside the public repo

Code, documentation, and reviewed fictional examples belong in this repo.
Broker exports, account metadata, and transaction ledgers may contain sensitive
information and should live in a separate private directory or repo selected
in the per-user TOML configuration.

A private Git repo controls repo access but does not encrypt its local
checkout, history, or backups. Backup, encryption, and secret-scanning policy is
still [planned work](todo.md).

The required setting in `~/.config/py-fund-manager/config.toml` makes the active
data location explicit without modifying this repo. The supplied sample setting
selects `sample-data/`; a private checkout can be selected instead. See the [CLI
overview](cli.md#data-root) for the configuration contract.
