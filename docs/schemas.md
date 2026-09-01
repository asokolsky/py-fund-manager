# Schemas

This document is the authoritative schema reference for canonical portfolio
data and generated plans. Directory placement, discovery, and persistence rules
belong in the [storage and validation
reference](portfolio-storage-validation.md). Input-file formats
belong in [Import Files](import-files.md).

All manifests use a strict `apiVersion`, `kind`, `metadata`, and `spec` envelope.
Unknown fields and an `apiVersion` other than the string `v1` are rejected.
Resource names begin with an ASCII letter or digit and may then contain ASCII
letters, digits, periods, underscores, and hyphens. Path separators and relative
path components are rejected.

The links in each section identify both the Pydantic model that defines the
schema and the application boundary that loads or constructs it. Pydantic field
and model validators enforce local shape and arithmetic; loaders and data-root
validation enforce cross-file identity, ordering, and reference rules.

## Portfolio

Source: [`Portfolio` and `PortfolioSpec`](../py_fund_manager/schemas.py#L53)
define the manifest. [`load_manifest`](../py_fund_manager/portfolio.py#L287)
performs strict YAML dispatch and model validation, while
[`find_manifest_in`](../py_fund_manager/portfolio.py#L374) enforces uniqueness
and the containing-directory identity.

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

Source: [`Transaction`](../py_fund_manager/schemas.py#L89) defines each ledger
fact. [`load_transactions`](../py_fund_manager/portfolio.py#L412) validates CSV
rows through that model, then
[`validate_transaction_ledger`](../py_fund_manager/portfolio.py#L428) enforces
ledger-wide chronology and unique identities. Opening and activity imports enter
through [`import_opening_snapshot`](../py_fund_manager/portfolio.py#L150) and
[`import_activity`](../py_fund_manager/portfolio.py#L195).

`transactions.csv` has these columns:

```csv
id,occurred_at,type,ticker,quantity,price,amount,cost_basis,currency,fees,external_id
opening-000001,2026-08-26T09:00:00-07:00,opening_position,AAPL,12,,,2100.00,USD,,broker-position-1
```

Rows must be chronological. `id` values and nonempty `external_id` values must
each be unique across the ledger. Timestamps must include a timezone offset.
Prices, amounts, cost basis, and fees cannot be negative.

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

Source: [`TargetAllocation`, `StrategySpec`, and
`Strategy`](../py_fund_manager/schemas.py#L183) define the allocation and
manifest. [`load_manifest`](../py_fund_manager/portfolio.py#L287) validates the
document, and [`find_manifest_in`](../py_fund_manager/portfolio.py#L374) enforces
the resource-directory identity.

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

Source: [`StrategyRevisionReference`, `StrategyAssignment`, and
`StrategyHistory`](../py_fund_manager/schemas.py#L261) define references and
ordered assignments. [`load_strategy_history`](../py_fund_manager/strategy.py#L109)
loads the manifest, [`effective_assignment`](../py_fund_manager/strategy.py#L118)
selects an assignment for a requested time, and
[`load_strategy_revision`](../py_fund_manager/strategy.py#L90) enforces revision
identity and content integrity. Complete data-root validation checks directory
identity and every referenced revision in
[`_validate_portfolios`](../py_fund_manager/validation.py#L76).

```yaml
apiVersion: v1
kind: StrategyHistory
metadata:
  name: etrade-roth-ira
spec:
  assignments:
    - id: initial-strategy
      effective_at: 2026-01-01T09:00:00-08:00
      strategy:
        name: mag7
        revision: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      reason: Initial portfolio strategy
```

`metadata.name` must equal the containing portfolio directory. The revision uses
`sha256:` followed by 64 lowercase hexadecimal digits. Assignment IDs must be
unique and nonempty. Effective times must include a timezone offset, appear in
strictly increasing order, and cannot be shared. Reasons are optional nonempty
text.

Each strategy name and revision must resolve to validated immutable strategy
content. For a requested time, the active assignment is the last assignment whose
`effective_at` is not later than that time.

## Rebalance plan

Source: [`RebalanceOrder`, `RebalanceValuation`, `RebalanceSummary`, and
`RebalancePlan`](../py_fund_manager/schemas.py#L430) define the complete output
and its reconciliation validators.
[`plan_rebalance`](../py_fund_manager/rebalance.py#L190) constructs the plan from
validated portfolio state, strategy, and prices. Broker execution reloads JSON
through [`load_rebalance_plan`](../py_fund_manager/__main__.py#L271), which runs
the same Pydantic validation before any order is submitted. The shared
[`normalize_cash_flow_amount`](../py_fund_manager/schemas.py#L564) constraint
enforces exact cents and the fixed 18-integer-digit currency limit for both CLI
and library callers.

The rebalance command emits a validated JSON document to standard output:

```json
{
  "schema_version": 1,
  "portfolio_id": "brokerage",
  "strategy_assignment_id": "assignment-example",
  "strategy": {
    "id": "SnP500-direct",
    "revision": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "generated_at": "2026-08-26T14:00:01-07:00",
  "valuation": {
    "as_of": "2026-08-26T14:00:00-07:00",
    "currency": "USD",
    "holdings_value": "95000.00",
    "available_cash": "5000.00",
    "withdrawal": "0.00",
    "target_portfolio_value": "100000.00"
  },
  "orders": [
    {
      "ticker": "AAPL",
      "side": "buy",
      "current_quantity": "10",
      "current_value": "2200.00",
      "target_weight": "0.061234567890",
      "target_value": "6123.46",
      "estimated_price": "220.00",
      "price_as_of": "2026-08-26",
      "price_available_at": "2026-08-26T16:00:00-04:00",
      "price_source": "Yahoo Finance via yfinance",
      "price_source_partition": "interval=1d/ticker=AAPL/year=2026/data.parquet",
      "quantity": "17.833894",
      "estimated_notional": "3923.456680",
      "reason": "underweight"
    }
  ],
  "summary": {
    "buy_orders": 1,
    "sell_orders": 0,
    "estimated_buys": "3923.456680",
    "estimated_sells": "0.00",
    "estimated_ending_cash": "1076.543320"
  },
  "warnings": []
}
```

Decimal values serialize as JSON strings. Order sides are `buy` or `sell`; reasons
are `underweight`, `overweight`, or `not_in_strategy`. The plan identifies the
effective assignment and immutable revision. It is neither a broker order nor an
execution report and is never written to the transaction ledger.

Version `1` requires confirmed ledger cash rather than a contribution assumption,
plus exact execution arithmetic, price availability, and source provenance. Each
estimated notional equals rounded quantity multiplied by estimated price. Summary
amounts reconcile to the order notionals, preserving residual cash from
fractional-quantity rounding. Consumers must reject unsupported versions.
