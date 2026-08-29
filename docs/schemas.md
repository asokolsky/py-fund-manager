# Schemas

This document is the authoritative schema reference for canonical portfolio
data and generated plans. Directory placement, discovery, and persistence rules
belong in the [storage and validation reference](README.md). Input-file formats
belong in [Import Files](import-files.md).

All manifests use a strict `apiVersion`, `kind`, `metadata`, and `spec` envelope.
Unknown fields and an `apiVersion` other than the string `v1` are rejected.
Resource names begin with an ASCII letter or digit and may then contain ASCII
letters, digits, periods, underscores, and hyphens. Path separators and relative
path components are rejected.

## Portfolio

```yaml
apiVersion: v1
kind: Portfolio
metadata:
  name: etrade-roth-ira
  display_name: E*TRADE Roth IRA
spec:
  broker: etrade
  account_id: etrade-roth-ira
  base_currency: USD
```

The current creation command derives `broker` from the first segment of the
portfolio ID and uses the ID for both metadata names and the local `account_id`.
These are bootstrap defaults, not claims about the displayed account name or
account number. Account-opening state belongs in the transaction opening
boundary, and strategy selection belongs in `StrategyHistory`.

Portfolio IDs use lowercase kebab-case. Currency is normalized to a
three-character uppercase code. `metadata.name` must equal the containing
portfolio directory name.

## Transaction

`transactions.csv` has these columns:

```csv
id,occurred_at,type,ticker,quantity,price,amount,cost_basis,currency,fees,external_id
opening-000001,2026-08-26T12:00:00+00:00,opening_position,AAPL,12,,,2100.00,USD,,broker-position-1
```

Rows must be chronological. `id` values and nonempty `external_id` values must
each be unique across the ledger. Timestamps must include a UTC offset. Prices,
amounts, cost basis, and fees cannot be negative.

`id` is the application's local ledger identity. `external_id` is optional in the
canonical ledger but required for ongoing activity imports, where it identifies
the immutable event in the source system and makes overlapping exports
idempotent.

Supported transaction types are:

- `opening_position` and `opening_cash`
- `position_adjustment`
- `buy` and `sell`
- `dividend`, `interest`, and `fee`
- `deposit` and `withdrawal`
- `split`
- `transfer_in` and `transfer_out`

Position-changing security events require a ticker and quantity. Cash events
require `amount`. Trades require `amount` or `price`. An `opening_cash` row cannot
carry security fields, and an `opening_position` row cannot carry `amount` or
`price`.

Rebalance state derivation treats `opening_position`, `buy`, and `transfer_in` as
position increases; `sell` and `transfer_out` as decreases; and
`position_adjustment` as a signed change. Trade cash uses `amount` when present or
`price × quantity` otherwise, and fees reduce cash. Split derivation and
non-base-currency transactions are rejected until their accounting rules are
implemented.

## Strategy

```yaml
apiVersion: v1
kind: Strategy
metadata:
  name: mag7
  display_name: Magnificent Seven equal weight
spec:
  allocation:
    type: target_weights
    positions:
      AAPL: "0.142857"
      AMZN: "0.142857"
      GOOGL: "0.142857"
      META: "0.142857"
      MSFT: "0.142857"
      NVDA: "0.142857"
      TSLA: "0.142858"
```

Tickers are normalized to uppercase. Positions must be nonempty, weights must be
nonnegative, and the validated total must equal `1.0` within the documented
tolerance. `metadata.name` must equal the containing strategy directory name.
Existing mixed-case identities such as `SnP500-direct` remain valid.

## StrategyHistory

```yaml
apiVersion: v1
kind: StrategyHistory
metadata:
  name: etrade-roth-ira
spec:
  assignments:
    - id: initial-strategy
      effective_at: 2026-01-01T00:00:00Z
      strategy:
        name: mag7
        revision: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      reason: Initial portfolio strategy
```

`metadata.name` must equal the containing portfolio directory. The revision uses
`sha256:` followed by 64 lowercase hexadecimal digits. Assignment IDs must be
unique and nonempty. Effective times must include a UTC offset, appear in strictly
increasing order, and cannot be shared. Reasons are optional nonempty text.

Each strategy name and revision must resolve to validated immutable strategy
content. For a requested time, the active assignment is the last assignment whose
`effective_at` is not later than that time.

## Rebalance plan

The rebalance command emits a validated JSON document to standard output:

```json
{
  "schema_version": 2,
  "portfolio_id": "etrade-brokerage",
  "strategy_assignment_id": "assignment-example",
  "strategy": {
    "id": "SnP500-direct",
    "revision": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "generated_at": "2026-08-26T21:00:01Z",
  "valuation": {
    "as_of": "2026-08-26T21:00:00Z",
    "currency": "USD",
    "holdings_value": "95000.00",
    "available_cash": "5000.00",
    "contribution": "10000.00",
    "withdrawal": "0.00",
    "target_portfolio_value": "110000.00"
  },
  "orders": [
    {
      "ticker": "AAPL",
      "side": "buy",
      "current_quantity": "10",
      "current_value": "2200.00",
      "target_weight": "0.061234567890",
      "target_value": "6735.80",
      "estimated_price": "220.00",
      "price_as_of": "2026-08-26",
      "price_available_at": "2026-08-26T16:00:00-04:00",
      "price_source": "Yahoo Finance via yfinance",
      "price_source_partition": "interval=1d/ticker=AAPL/year=2026/data.parquet",
      "quantity": "20.617272",
      "estimated_notional": "4535.799840",
      "reason": "underweight"
    }
  ],
  "summary": {
    "buy_orders": 1,
    "sell_orders": 0,
    "estimated_buys": "4535.799840",
    "estimated_sells": "0.00",
    "estimated_ending_cash": "10464.200160"
  },
  "warnings": []
}
```

Decimal values serialize as JSON strings. Order sides are `buy` or `sell`; reasons
are `underweight`, `overweight`, or `not_in_strategy`. The plan identifies the
effective assignment and immutable revision. It is neither a broker order nor an
execution report and is never written to the transaction ledger.

Version `2` requires exact execution arithmetic plus price availability and
source provenance. Each estimated notional equals rounded quantity multiplied by
estimated price. Summary amounts reconcile to the order notionals, preserving
residual cash from fractional-quantity rounding. Consumers must reject unsupported
versions.
