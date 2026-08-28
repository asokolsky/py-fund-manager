# S&P 500 Strategy Reference

This directory contains a direct-replication strategy intended to track the
[S&P 500](https://en.wikipedia.org/wiki/S%26P_500). It is not a portfolio and does
not represent an actual brokerage account.

`strategy.yaml` is generated from the ignored SPY holdings workbook by
`generate_strategy.py` and follows the validated schema in
[docs/README.md](../../../docs/README.md). Regenerate it from the repo root with:

```shell
mise exec -- uv run python \
  sample-data/strategy/SnP500-direct/generate_strategy.py
```

## Assumptions

- SPY holdings are used as an investable proxy for S&P 500 constituent weights.
- The source snapshot is dated August 6, 2026 and contains 503 equity rows,
  reflecting multiple listed share classes for some index companies.
- The US-dollar cash row and the contra-account row are excluded because this
  strategy represents equity constituents only.
- The remaining published equity weights total `99.912299%`; they are normalized
  proportionally to `1.0` without changing their relative weights.
- Weights are emitted with 12 decimal places. Any rounding residual is applied to
  the final position so the persisted allocation totals exactly `1.0`.
- Source tickers are preserved, including dot-form share classes such as `BRK.B`
  and `BF.B`. Broker-specific symbol conversion belongs in order generation.
- The generated allocation is a dated snapshot, not a timeless definition of
  index membership. It must be regenerated when a new source workbook is adopted.
- The workbook is source material only and remains ignored by Git; the generated
  YAML and its documented provenance are reviewed source files.

References:

- [S&P U.S. Indices Methodology](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf)
- [SPY daily holdings](https://www.ssga.com/us/en/individual/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx)

Remaining source-acquisition and licensing work is tracked in
[docs/todo.md](../../../docs/todo.md).
