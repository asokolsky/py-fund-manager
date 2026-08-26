# S&P 500 Strategy Reference

This directory contains source material for a future strategy intended to track
the [S&P 500](https://en.wikipedia.org/wiki/S%26P_500). It is not a portfolio and
does not represent an actual brokerage account.

The existing `index.yaml` contains legacy name and ticker metadata. It predates
the validated `strategy.yaml` schema documented in
[docs/README.md](../../../docs/README.md)
and is not loaded by the application. The checked-in SPY holdings workbook is a
reference snapshot from the linked provider, not authoritative application data.

References:

- [S&P U.S. Indices Methodology](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf)
- [SPY daily holdings](https://www.ssga.com/us/en/individual/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx)

Migration and provenance work is tracked in [docs/todo.md](../../../docs/todo.md).
