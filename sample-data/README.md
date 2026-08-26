# Sample Data Root

This directory is an explicit, fictional data root that follows the storage
contract in [docs/README.md](../docs/README.md):

```text
sample-data/
├── portfolios/
│   └── sample/
└── strategies/
    ├── two-stock-example/
    └── SnP500/
```

Never replace the fictional values with broker exports or actual account
identifiers.

The sample per-user configuration in
[`docs/config.toml.example`](../docs/config.toml.example) selects this directory.
New portfolio directories created below `sample-data/portfolios/` are ignored by
Git unless explicitly added as reviewed fictional samples.

## Store real data in a private repo

Do not add broker exports, account identifiers, or real transaction history to
this public repo. Create a separate Git repo with private access for that data.
Its root is the application data root and should contain the same top-level data
directories as the sample:

```text
py-fund-manager-data/
├── .git/
├── portfolios/
└── strategies/
```

A practical setup is:

1. Create a private repo in the Git hosting service used for personal data.
2. Clone it into a local directory separate from this public repo.
3. Create `portfolios/` and `strategies/` in that repo.
4. Set `data.root` in the per-user configuration to the private repo's local
   directory.
5. Commit and push real portfolio data only to the private repo.

For example, configure a private repo stored under `~/PersonalProjects` in
`~/.config/py-fund-manager/config.toml`:

```toml
[data]
root = "~/PersonalProjects/py-fund-manager-data"
```

See [portfolios/README.md](portfolios/README.md) for the sample portfolio contents
and the [CLI overview](../docs/cli.md#data-root) for configuration installation
and path resolution.

Private-repo permissions restrict who can fetch the repo, but they do not encrypt
its local files, Git history, or backups. Apply suitable encryption, backup, and
retention policies separately.
