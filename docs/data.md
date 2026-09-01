# Sample and Personal Data

The application uses the same data-root contract for fictional samples and real
portfolio data. This repo includes a reviewed sample root; personal data belongs
in a separate private location.

## Sample data root

[`sample-data/`](../sample-data/) is an explicit, fictional data root:

```text
sample-data/
├── portfolio/
│   └── sample/
└── strategy/
    ├── mag7/
    └── SnP500-direct/
```

Do not replace its fictional values with broker exports or actual account
identifiers. New portfolio directories created below `sample-data/portfolio/`
are ignored by Git unless explicitly added as reviewed fictional samples.

The supplied [`config.toml.example`](config.toml.example) selects this sample
root so it is safe to use while evaluating the project. See the [sample
portfolio guide](../sample-data/portfolio/README.md) for its contents.

## Personal data root

Personal portfolio data does not belong in this public repo. Store real broker
exports, account records, portfolios, and strategies in a separate private repo
or another private directory.

The private data root may be the checkout root of a private Git repo. A typical
sibling-checkout layout is:

```text
PersonalProjects/
├── py-fund-manager/          # this public code repo
└── py-fund-manager-data/     # separate private repo and data root
    ├── .git/
    ├── portfolio/
    │   └── etrade-roth-ira/
    │       ├── account.yaml
    │       ├── allocation-history.yaml
    │       ├── transactions.csv
    │       └── imports/
    │           └── opening.csv
    └── strategy/
```

The checkout may live anywhere appropriate; do not record its location in this
public repo. The CLI creates `portfolio/` below the selected root. Strategy
commands use the `strategy/` hierarchy and portfolio strategy history.

To set up a private Git repo safely:

1. Create a repo with private access in the Git hosting service used for
   personal data.
2. Clone it into a directory separate from this public repo. If initializing it
   locally, configure a private remote before publishing anything.
3. Create `portfolio/` and `strategy/` in the private repo and select its root
   in the per-user configuration.
4. Commit and push real portfolio data only to the private repo.

Private-repo access controls do not encrypt the local checkout, Git history, or
backups. Apply suitable encryption, backup, and retention controls separately;
stronger defaults remain [planned
work](todo.md#supporting-design-and-validation).

## Configure the CLI

Copy [`config.toml.example`](config.toml.example) to
`~/.config/py-fund-manager/config.toml`, then update `data.root` to select the
private data root:

```toml
[data]
root = "~/PersonalProjects/py-fund-manager-data"
```

The setting is required. See the [CLI data-root
reference](cli.md#data-root) for path-resolution details, the [storage
contract](portfolio-storage-validation.md#directories-and-data-roots) for the
directory layout, and the [validation command](cli-validate.md) for checking the
selected root.
