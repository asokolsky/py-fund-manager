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
- **TODO: Analytics.** Calculate annualized and benchmark-relative performance,
  holding-period results, and realized gains or losses.
- **TODO: Broker order adaptation.** Translate broker-neutral rebalance plans into
  whole-share or fractional orders with broker minimums, price precision, tax-lot
  selection, limit-price policy, and explicit submission approval.
- **TODO: Terminal UI.** Browse portfolios through a terminal interface.

## Supporting design and validation

- **TODO: Ledger invariants.** Validate chronological ordering, unique
  `external_id` values where present, transaction-type-specific cash fields, and
  cross-row consistency.
- **TODO: Portfolio metadata.** Add creation options or an edit command for
  display name, broker, redacted account identifier, base currency, and opening
  date. Strategy selection belongs to strategy history.
- **TODO: Private-data hardening.** Define backup, encryption, and secret-scanning
  policies for the separate private data root documented in the
  [sample portfolio guide](../sample-data/portfolios/README.md).

## S&P 500 source maintenance

- **TODO: Source acquisition and licensing.** Automate acquisition of dated SPY
  holdings and confirm the terms for retaining and transforming the source. The
  checked-in generator already handles normalization and deterministic strategy
  regeneration.
