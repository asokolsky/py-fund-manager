# Validate CLI

The `validate` command verifies the complete configured data root without writing
files or making network requests. It uses the same strict YAML loader, Pydantic
models, resource discovery, and reference resolution as normal operations.

## Run validation

Configure the [data root](cli.md#data-root), then run:

```shell
mise run py-fund-manager -- validate
```

A successful run prints a resource summary and exits with status `0`:

```text
Validated 1 Portfolio, 2 Strategies, 1 StrategyHistory, and 1 revision.
```

Validation failures are written to standard error and produce status `1`. The
messages include the relevant path and, where available, the invalid field or
reference. Independent resource errors are reported together where practical so
one run can expose more than one problem.

## What is verified

The command scans every resource directory below `portfolio/` and `strategy/`
and verifies:

- strict single-document YAML parsing, including duplicate-key rejection;
- the `apiVersion: v1`, `kind`, `metadata`, and `spec` manifest envelope;
- supported resource kinds and fields;
- exactly one current Portfolio or Strategy in each corresponding directory;
- `metadata.name` agreement with the containing directory;
- at most one StrategyHistory per Portfolio;
- transaction ledger syntax and semantics when `transactions.csv` exists;
- every StrategyHistory assignment and its Strategy name and revision reference;
- every immutable Strategy revision, including manifest identity, content digest,
  and filename digest.

Current manifests are discovered by `kind`, not by filename. Strategy revision
files below `revisions/` are checked separately and are not treated as current
Strategy manifests.

## Operational guarantees

Validation does not create directories, rewrite manifests, generate revisions,
change timestamps, download prices, or contact a broker or market-data provider.
This makes it suitable for local checks and continuous integration before data is
used by portfolio operations.

The command validates configuration before scanning data. A missing configuration
file, missing `data.root`, nonexistent root, or non-directory root is an error.
See the [storage and validation
contract](portfolio-storage-validation.md) for the complete manifest and
directory rules.
