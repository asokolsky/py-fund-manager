# Concepts

This document defines the domain model independently of file formats and CLI
commands. See the [storage and validation contract](README.md) for persistence,
schemas, directory layout, and implementation status.

## Portfolio

A **portfolio** represents one actual investment account. It has identity and
configuration, but its holdings and cash are derived state rather than separately
editable facts.

Each portfolio has its own accounting history. Two accounts following the same
investment strategy remain separate portfolios because their transactions, tax
lots, cash, fees, and performance differ.

## Opening boundary

Portfolio accounting needs a starting point. When complete broker history is
available, account state can be derived from that history. When it is not, an
**opening boundary** establishes known positions and cash at an effective time.

Facts before the boundary are unknown unless they are imported later. Consequently,
cost basis, holding period, and performance before that boundary may be unavailable
or incomplete. The opening state plus every later transaction defines the account
state after the boundary.

## Transaction and ledger

A **transaction** is an immutable account fact, such as an opening position, buy,
sell, dividend, fee, deposit, withdrawal, split, or transfer. A proposed action is
not a transaction until execution is confirmed.

A **transaction ledger** is the ordered collection of those uniquely identified
facts. Neither timestamp nor ticker is a unique key: several events may share a
timestamp, and one security may have many events. Date and ticker indexes are
derived views over the ledger.

Corrections should be additional explicit facts rather than silent changes to
earlier records. This preserves the audit trail and makes reconciliation results
explainable.

## Position, holding, and tax lot

A **position** or **holding** is the derived quantity of a security in a portfolio.
The terms are equivalent at the aggregate ticker level in this project.

A **tax lot** is a portion of a position with its own acquisition date and cost
basis. One ticker may therefore have one aggregate position but several tax lots.
The persistent lot model remains an open design question.

## Strategy

A **strategy** describes desired allocation weights and may name a benchmark. It
does not contain actual account holdings or transaction history. Multiple
portfolios may follow the same strategy.

Applying a strategy to current portfolio state and market prices produces a
proposed **order plan** for contribution, withdrawal, or rebalancing. The plan is
advice; its orders become ledger transactions only after broker execution is
confirmed.

A **strategy assignment** associates a portfolio with one immutable revision of a
strategy from an effective time. It records investment intent, not broker activity,
so it is not a transaction. Changing an assignment does not change holdings or
cash and does not generate orders automatically.

For a requested time, the active assignment is the assignment with the latest
effective time that is not later than the requested time. This permits historical
valuation and order plans to identify the exact allocation that governed the
portfolio. An order plan records both the assignment ID and strategy revision used
to produce it.

A strategy ID identifies the continuing allocation policy. A **strategy revision**
identifies exact strategy content. Revisions are immutable because constituents
and weights may change while the strategy ID remains stable.

“Strategy” is the chosen term for allocation rules. “Fund” can refer to a pooled
legal vehicle and is too ambiguous here, although it remains in legacy material
and planned CLI examples.

## Market-price cache

The **market-price cache** contains externally retrieved price observations. It is
neither account state nor transaction history. Portfolio valuation combines
derived positions with prices for a requested time, while performance calculations
also account for cash flows and corporate actions.

Price observations can be regenerated from their provider. Portfolio facts cannot
be reconstructed from prices and therefore have a different retention and privacy
boundary.

## Relationship summary

```text
opening boundary + confirmed transactions
                 │
                 ▼
        derived portfolio state ──┬── market prices ──► valuation/performance
                                  │
effective strategy assignment ───┐
strategy revision + market prices ┴──────────────────► proposed order plan
                                                               │
                                                    confirmed execution
                                                               │
                                                               ▼
                                                     new transactions
```
