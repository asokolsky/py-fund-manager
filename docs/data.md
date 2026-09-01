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

## Data root configuration

Portfolio commands read the data root from the per-user configuration file:

```text
~/.config/py-fund-manager/config.toml
```

Set the checkout or directory in TOML:

```toml
[data]
root = "~/PersonalProjects/py-fund-manager-data"
```

[`config.toml.example`](config.toml.example) is a copyable per-user sample that
points to this repo's `sample-data/` directory, assuming the repo is checked out
at `~/PersonalProjects/py-fund-manager`. Adjust its root when the checkout lives
elsewhere, then install it as
`~/.config/py-fund-manager/config.toml`.

`XDG_CONFIG_HOME` replaces `~/.config` when that environment variable is set.
The root may be absolute, start with `~`, or be relative to the configuration
file. The configuration file, `data.root` setting, and selected directory are
required.

See the [storage
contract](portfolio-storage-validation.md#directories-and-data-roots) for the
directory layout and the [validation command](cli-validate.md) for checking the
selected root.
