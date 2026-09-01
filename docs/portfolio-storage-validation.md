# Portfolio Storage and Validation

This document is the authoritative storage and validation reference for portfolio
data. Validated shapes belong in [Schemas](schemas.md), input formats belong in
[Import Files](import-files.md), rationale belongs in
[Design Decisions](design-decisions.md), and unimplemented work belongs in
[Planned Work](todo.md).

## Conceptual model

[Concepts](concepts.md) defines the domain abstractions and their relationships.
This contract specifies their directories, file formats, and validation behavior.
[Sample and Personal Data](data.md) explains how to select a fictional or private
data root that follows this contract.

## Directories and data roots

A **data root** is an existing directory containing `portfolio/` and
`strategy/`:

```text
DATA_ROOT/
├── portfolio/
│   └── etrade-roth-ira/
│       ├── account.yaml
│       ├── allocation-history.yaml
│       ├── transactions.csv
│       └── imports/
│           └── opening.csv
└── strategy/
    └── mag7/
        ├── current.yaml
        └── README.md
```

Real broker exports and account records should live in a separate private data
root. [Sample and Personal Data](data.md) defines that boundary, describes the
fictional data shipped here, and explains how to configure a private checkout.

The generated
[`sample-data/strategy/SnP500-direct/`](../sample-data/strategy/SnP500-direct/README.md)
strategy is documented separately.

Top-level YAML filenames within a resource directory are descriptive conventions,
not schema. The application strictly parses each `*.yaml` file and discovers its
type from `apiVersion` and `kind`. A portfolio directory contains exactly one
`Portfolio` and at most one `StrategyHistory`; a strategy directory contains
exactly one current `Strategy`. Immutable files below `revisions/` are excluded
from current-resource discovery. Canonical files contain exactly one manifest;
multi-document YAML streams are rejected.

Every subdirectory below `portfolio/` and `strategy/` is validated as a resource
directory. A README alone does not disable validation. A documentation-only
scenario must contain the explicit `.py-fund-manager-documentation-only` marker
and may contain only that marker, `README.md`, and `.gitignore`.

## Data contracts

[Schemas](schemas.md) defines the canonical Portfolio, Transaction, Strategy,
StrategyHistory, and rebalance-plan shapes. [Import Files](import-files.md)
defines every accepted input format and its preservation behavior.

## Strategy revision storage

A strategy ID may have multiple immutable revisions as its constituents or weights
change:

```text
strategy/SnP500-direct/
├── strategy.yaml
└── revisions/
    └── sha256-<64-lowercase-hex-digits>.yaml
```

The revision is the SHA-256 digest of canonical validated strategy content, not
incidental YAML formatting. Canonicalization serializes the complete model in JSON
mode with aliases, explicit null values, sorted object keys, compact separators,
UTF-8 encoding, and no trailing newline. Decimal values remain JSON strings.

Creating an assignment snapshots that content if the revision is not already
present. A revision file is never replaced. Existing assignments cannot be edited
or removed through normal commands. A new assignment is appended after validating
the full history, and the YAML file is replaced atomically. Recording an
assignment neither rebalances the portfolio nor writes financial transactions.

## CLI usage

See the [validation CLI guide](cli-validate.md), [portfolio CLI
guide](cli-portfolio.md), and [strategy CLI guide](cli-strategy.md). General
invocation and data-root selection are documented in the [CLI overview](cli.md).

For a complete historical walkthrough—from portfolio creation through confirmed
deposits, rebalancing, broker execution, imports, and withdrawals—see the
[Playground portfolio guide](../sample-data/portfolio/playground/README.md).
