# Planned Work

This document is the single list of capabilities and design work that remain
unimplemented. Items stay here until they are addressed.

## Capability backlog

- **TODO: Broker activity and tax lots.** Define the canonical broker-activity
  schema and import trades, dividends, interest, fees, deposits, withdrawals,
  splits, transfers, and later holdings snapshots. Decide whether lot identity
  and acquisition date belong on transaction rows or in a related lot-allocation
  file, including multiple lots for one ticker.
- **TODO: Cash bootstrap and effective time.** Add canonical cash input, emit
  `opening_cash` transactions during initial import, and accept an explicit
  statement `as_of` value instead of using import time.
- **TODO: Reconciliation.** Compare later broker snapshots with derived positions
  and cash, report differences, and require explicit approval before writing
  `position_adjustment` rows.
- **TODO: Derived state and analytics.** Calculate holdings, cash, portfolio value,
  annualized and benchmark-relative performance, holding-period results, and
  realized gains or losses.
- **TODO: Strategy history schemas.** Add Pydantic models for effective-dated
  strategy assignments, histories, and revision references using the documented
  `strategy-history.yaml` contract.
- **TODO: Strategy revisions.** Canonicalize validated strategy content, calculate
  its SHA-256 revision, and create immutable content-addressed snapshots.
- **TODO: Strategy assignment operations.** Load and atomically update append-only
  strategy history, resolve the assignment effective at a requested time, and add
  show, history, and set commands.
- **TODO: Legacy strategy migration.** Convert the optional `portfolio.yaml`
  strategy pointer into an initial assignment, remove the pointer, and update the
  sample portfolio after the history schema is implemented.
- **TODO: Rebalance planning.** Produce contribution, withdrawal, and rebalance
  order plans using the effective assignment and immutable strategy revision.
- **TODO: Terminal UI.** Browse portfolios through a terminal interface.

## Supporting design and validation

- **TODO: Ledger invariants.** Validate chronological ordering, unique
  `external_id` values where present, transaction-type-specific cash fields, and
  cross-row consistency.
- **TODO: Portfolio metadata.** Add creation options or an edit command for
  display name, broker, redacted account identifier, base currency, opening
  and opening date. Strategy selection belongs to strategy history.
- **TODO: Private-data hardening.** Define backup, encryption, and secret-scanning
  policies for the separate private data root documented in the
  [sample portfolio guide](../sample-data/portfolios/README.md).

## S&P 500 source maintenance

- **TODO: Source acquisition and licensing.** Automate acquisition of dated SPY
  holdings and confirm the terms for retaining and transforming the source. The
  checked-in generator already handles normalization and deterministic strategy
  regeneration.
