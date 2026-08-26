# Sample Portfolios and Real Data

This directory contains fictional portfolios within the explicit
[`sample-data/`](../README.md) data root. It demonstrates the storage contract in
[`docs/README.md`](../../docs/README.md).

## Sample portfolio

[`sample/`](sample/) contains:

- `portfolio.yaml`: validated account metadata using fictional identifiers.
- `transactions.csv`: two opening positions with fixed example timestamps.
- `imports/stocks.csv`: the canonical holdings input represented by those opening
  transactions.

`sample-data/` is the sample data root because it contains this `portfolios/`
directory and the sibling `strategies/` directory. The sample is safe to inspect
and use in deterministic tests. Do not replace its values with broker exports or
actual account identifiers.

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
    └── portfolios/
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
