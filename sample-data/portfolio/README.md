# Sample Portfolios

This directory contains fictional portfolios within the explicit
[`sample-data/`](../README.md) data root. It demonstrates the storage contract in
[`docs/portfolio-storage-validation.md`](../../docs/portfolio-storage-validation.md).

## Sample portfolio

[`sample/`](sample/) contains:

- `portfolio.yaml`: validated account metadata using fictional identifiers.
- `strategy-history.yaml`: an effective-dated assignment to an immutable revision
  of the fictional equal-weight Magnificent Seven strategy.
- `transactions.csv`: two opening positions with fixed example timestamps.
- `imports/opening.csv`: the canonical snapshot represented by those opening
  transactions.

`sample-data/` is the sample data root because it contains this `portfolio/`
directory and the sibling `strategy/` directory. The sample is safe to inspect
and use in deterministic tests. Do not replace its values with broker exports or
actual account identifiers.

## Playground regression

[`playground/README.md`](playground/README.md) documents the temporary USD 100,000
Mag7 lifecycle exercised by `tests/playground_test.py`, including its synthetic
price assumptions, dividend update, second rebalance, and explicit test limits.

## Use a private data root

The sample does not contain a committed pointer or reference to real data. The
[Sample and Personal Data](../../docs/data.md) explains how to keep real account
data separate and select a private data root. See the [portfolio CLI
guide](../../docs/cli-portfolio.md) for creation and bootstrap commands.

`.gitignore` ignores any additional portfolio directory created here;
only the fictional `sample/` tree is intended for version control.
