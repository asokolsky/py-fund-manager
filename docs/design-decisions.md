# Design Decisions

This document records settled storage and validation choices. See
[Concepts](concepts.md) for the domain model and [Portfolio Storage and
Validation](portfolio-storage-validation.md) for the resulting contract.

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

## YAML resources use a versioned manifest envelope

Authored YAML uses `apiVersion: v1`, a strict `kind`, `metadata`, and `spec`.
`metadata.name` is stable machine identity, `metadata.display_name` preserves a
human-readable Portfolio or Strategy name, and `spec` contains desired domain
configuration. Account-opening state remains at the transaction boundary rather
than in Portfolio configuration.

The application discovers current resources by kind among top-level YAML files in
singular `portfolio/<name>/` and `strategy/<name>/` directories. Filenames remain
descriptive conventions rather than type declarations. Resource directories keep
transactions, imports, and immutable revisions within bounded ownership and
atomicity boundaries. Revision directories are not scanned as current resources.

Canonical storage accepts one manifest per file. Strict parsing rejects duplicate
keys and multi-document streams. A portable multi-resource bundle would need its
own identity, reference, and atomic-application contract and is therefore outside
the canonical storage model.

## Strategy history uses YAML

Strategy assignments are infrequent, human-reviewed configuration events with
nested strategy identity, revision, and optional rationale. They are stored in a
separate StrategyHistory manifest, not forced into transaction-shaped CSV rows or
mixed with stable Portfolio identity.

The assignment list is append-only in the domain model. Because it remains small,
the application validates and atomically rewrites the complete YAML document when
adding an assignment. Existing entries cannot be edited or removed through normal
commands. A strategy change records intent only; it does not create transactions
or automatically place or propose trades.

StrategyHistory is the sole authority for effective strategy selection. Portfolio
does not duplicate the current strategy as a second source of truth.

## Strategy revisions are immutable

A stable Strategy name may acquire new constituents and weights. Each assignment
therefore records both the name and a SHA-256 revision derived from canonical
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

## Broker adapters use structural contracts

Broker code lives below `src/py_fund_manager/broker/`. The package separates shared
execution orchestration from concrete adapters: `execution.py` owns the
transport-neutral protocol and plan-to-fill workflow, `imports.py` dispatches
broker-native files to generic portfolio persistence, and `historical.py` and
`ibkr.py` own provider-specific behavior. Portfolio persistence accepts a
generic activity reader and does not import broker modules. The package
`__init__.py` conventionally re-exports stable public execution contracts so
callers do not depend on internal module layout. Future adapters follow the same
`broker/<name>.py` pattern.

The `Broker` protocol adapts a planned `BrokerOrder` to broker-supported values,
then fulfills it and returns confirmed `Execution` records. `HistoricalBroker`
defaults to E*TRADE-compatible three-decimal share quantities. Buys round down
to avoid exceeding their planned allocation; sells round up and are capped at
the available holding rounded down to the supported increment. Exact
full-liquidation quantities are preserved, and sub-increment orders or holdings
are omitted and reported. Quantity precision is configurable for simulating
another broker. It fills the adapted order using the
latest eligible observation from the historical price cache at its configured
execution time. Future live adapters can satisfy the same contract through
external APIs without inheriting simulation state.

Plan validation, order normalization, fill validation, and execution-to-ledger
mapping remain shared application services. The current synchronous workflow
requires fills to complete each submitted order exactly. Asynchronous order
status, cancellation, and resumable partial-fill workflows remain future broker
adapter work rather than implicit behavior in the common contract.

Deposits are confirmed ledger facts rather than rebalance-plan assumptions. A
deposit must be imported before planning, after which its cash is available to an
executable plan.

A withdrawal plan can be executed because its sell orders raise cash before the
money leaves the portfolio. Confirmed fills must leave at least the planned
withdrawal amount available. Execution produces only trade facts: it does not
silently convert the assumption into a withdrawal transaction. The operator must
import the fills and then import the separately confirmed withdrawal. Until that
second import occurs, the ledger still treats the reserved cash as available, so
a later plan could incorrectly reinvest money that has already left the account.

Normalized order IDs derive from the portfolio ID, the plan valuation timestamp
expressed in UTC, and the order's stable position in the plan. Re-executing the
same plan therefore produces the same order and execution identities. This makes
confirmed execution imports idempotent; IDs are unique per portfolio and plan
timestamp, not globally unique across independently generated plans with identical
timestamps.

## Pydantic models are the schema authority

All persisted inputs and structured outputs are defined as frozen Pydantic models
in `src/py_fund_manager/schemas.py`. Persisted-document
loaders reject duplicate keys, unknown fields, unsupported API versions or kinds,
invalid
identifiers and tickers, naive timestamps, invalid decimal values, and
transaction shapes missing required security fields. Examples in the storage
contract describe those models; they are not independent schemas.

## Private account data stays outside the public repo

Code, documentation, and reviewed fictional examples belong in this repo.
Broker exports, account metadata, and transaction ledgers may contain sensitive
information and should live in a separate private directory or repo selected
in the per-user TOML configuration. [Sample and Personal Data](data.md) documents
the supported layout and setup.

A private Git repo controls repo access but does not encrypt its local
checkout, history, or backups. Backup, encryption, and secret-scanning policy is
still [planned work](todo.md).

The required per-user setting makes the active data location explicit without
modifying this repo.
