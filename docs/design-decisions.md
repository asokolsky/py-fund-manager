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

## Strategy history uses YAML

Strategy assignments are infrequent, human-reviewed configuration events with
nested strategy identity, revision, and optional rationale. They are stored in a
separate `strategy-history.yaml`, not forced into transaction-shaped CSV rows or
mixed with stable account identity in `portfolio.yaml`.

The assignment list is append-only in the domain model. Because it remains small,
the application validates and atomically rewrites the complete YAML document when
adding an assignment. Existing entries cannot be edited or removed through normal
commands. A strategy change records intent only; it does not create transactions
or automatically place or propose trades.

`strategy-history.yaml` is the sole authority for effective strategy selection.
`portfolio.yaml` does not duplicate the current strategy as a second source of
truth.

## Strategy revisions are immutable

A stable strategy ID may acquire new constituents and weights. Each assignment
therefore records both the ID and a SHA-256 revision derived from canonical
validated content. Content-addressed revision snapshots remain available after the
editable strategy definition changes. Order plans record the assignment and
revision they used, preserving reproducibility without treating strategy intent as
broker activity.

## Decimal values preserve financial precision

Quantities, prices, amounts, fees, cost basis, and strategy weights use Python
`Decimal`, not binary floating-point values. Strategy weights are persisted as
quoted decimal fractions. They must be nonnegative and total `1.0` within a
`0.000001` tolerance. This retains more precision than integer thousandths or
basis points for small positions.

Rebalance buys are rounded down to the supported quantity increment to prevent
overspending. Sells are rounded up and capped at the current holding so a planned
withdrawal is fully funded without selling shares the portfolio does not own.
Estimated notionals are calculated afterward. Order notionals and summary cash
therefore retain the Decimal precision needed to satisfy `quantity × price`
exactly instead of hiding fractional residual cash behind cent rounding.

## Daily closes require coherent provenance and an availability time

A rebalance price is selected together with its trading date, currency, provider,
exchange timezone, and relative Parquet partition. Daily bars have no intraday
timestamp, so the planner treats a close as available from 16:00 in the recorded
exchange timezone. This conservative boundary prevents a same-day close from
being used before the regular session has ended. Missing provenance metadata and
conflicting latest observations are validation failures.

## Pydantic models are the schema authority

All persisted inputs and structured outputs are defined as frozen Pydantic models
in `py_fund_manager/schemas.py`. Persisted-document
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
