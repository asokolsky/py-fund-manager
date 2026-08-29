# Planned Work

This document is the single list of capabilities and design work that remain
unimplemented. Items stay here until they are addressed.

## Capability backlog

- **TODO: Integrated rebalance price refresh.** Before planning, derive the union
  of current and strategy tickers, refresh their current daily partitions with a
  bounded worker pool, and preserve successful atomic writes across partial
  failures. Use exchange calendars, exchange timezones, and a provider-publication
  delay to identify the expected latest completed session. Fail on missing or
  stale required observations by default; define an explicit
  `--allow-stale-prices` review override. Keep download and rebalance logic in
  shared application services. See the documented [price refresh
  workflow](cli-portfolio.md#price-refresh-workflow).
- **TODO: Tax lots and advanced broker activity.** Decide whether lot identity
  and acquisition date belong on transaction rows or in a related lot-allocation
  file, including multiple lots for one ticker. Add later holdings snapshots
  after their reconciliation semantics are defined.
- **TODO: Stock splits.** Add an activity representation with explicit numerator
  and denominator values for forward and reverse splits. Define aggregate-position
  and tax-lot quantity changes, total and per-share cost-basis treatment, and
  separate cash-in-lieu events for fractional shares before enabling split import
  or ledger derivation.
- **TODO: Reconciliation.** Compare later broker snapshots with derived positions
  and cash, report differences, and require explicit approval before writing
  `position_adjustment` rows. Require a successful reconciliation as a safety
  gate before converting a rebalance plan into broker orders.
- **TODO: Analytics.** Calculate annualized and benchmark-relative performance,
  holding-period results, and realized gains or losses.
- **TODO: Broker order adaptation.** Translate broker-neutral rebalance plans into
  whole-share or fractional orders with broker minimums, price precision, tax-lot
  selection, limit-price policy, and explicit submission approval. Reject stale
  plans and verify that no sell exceeds the reconciled position.
- **TODO: Terminal UI.** Browse portfolios through a terminal interface.

## Supporting design and validation

- **TODO: Rebalance controls.** Define minimum order amounts, weight-drift
  tolerances, configurable quantity increments, and a cash-flows-only mode that
  directs contributions toward underweight holdings without selling.
- **TODO: Rebalance plan provenance.** Add a deterministic plan ID, ledger and
  price-input fingerprints, and an expiration time so changed inputs or stale
  valuations cannot be submitted accidentally.
- **TODO: Remaining ledger invariants.** Define cross-row consistency for broker
  activity, tax lots, splits, and transfers beyond the enforced chronological
  ordering, unique transaction and external identities, and required cash/trade
  fields.
- **TODO: Portfolio metadata.** Add creation options or an edit command for
  display name, broker, redacted account identifier, base currency, and opening
  date. Strategy selection belongs to strategy history.
- **TODO: Private-data hardening.** Define backup, encryption, and secret-scanning
  policies for the separate private data root documented in the
  [sample portfolio guide](../sample-data/portfolio/README.md).

## S&P 500 source maintenance

- **TODO: Source acquisition and licensing.** Automate acquisition of dated SPY
  holdings and confirm the terms for retaining and transforming the source. The
  checked-in generator already handles normalization and deterministic strategy
  regeneration.
