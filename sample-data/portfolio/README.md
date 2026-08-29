# Sample Portfolios and Real Data

This directory contains fictional portfolios within the explicit
[`sample-data/`](../README.md) data root. It demonstrates the storage contract in
[`docs/README.md`](../../docs/README.md).

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

## Point the CLI at real data

The sample does not contain a committed pointer or reference to real data. The
[CLI overview](../../docs/cli.md#data-root) explains how the per-user TOML setting
selects a data root.

That root can be a separate private Git repo checked out beside the public code
repo:

```text
PersonalProjects/
├── py-fund-manager/       # public code and fictional sample
└── py-fund-manager-data/  # private Git repo and real data root
    ├── .git/
    └── portfolio/
```

If the private repo already exists remotely, clone it as the sibling
`py-fund-manager-data/` directory. For a new local private repo, create the
directory, initialize Git there, and configure a private remote before publishing
anything. See the [portfolio CLI guide](../../docs/cli-portfolio.md) for creation
and bootstrap commands.

`.gitignore` ignores any additional portfolio directory created here;
only the fictional `sample/` tree is intended for version control. A separate
private repo or encrypted, backed-up directory remains the preferred location for
real account data. Private-repo access restrictions do not encrypt the working
copy, Git history, or backups; apply encryption and retention controls separately.
