# py-fund-manager

`py-fund-manager` is a Python library and CLI for maintaining portfolio data and
downloading historical market prices.

## Current capabilities

- Create a portfolio with validated YAML metadata.
- Bootstrap opening positions and cash from a canonical CSV snapshot.
- Import timestamped broker activity with idempotent source identities.
- Store positions in a validated, append-oriented transaction ledger.
- Assign effective-dated strategies with immutable content revisions.
- Validate strict YAML manifests, resource identities, and revision references.
- Generate strict JSON rebalance plans from confirmed cash and planned withdrawals.
- Simulate reviewed rebalance plans deterministically at recorded historical prices.
- Download Yahoo Finance prices into atomic, year-partitioned Parquet files.

See [planned work](docs/todo.md) for capabilities that are not implemented yet.

## Repo structure

- `py_fund_manager/` contains the Python package, CLI, configuration loader,
  schemas, and portfolio persistence operations.
- [`tests/`](tests/README.md) contains deterministic unit tests and instructions
  for running the complete suite or selected tests.
- [`docs/`](docs/README.md) contains concepts, design decisions, schemas, CLI
  guides, and planned work.
- [`sample-data/`](sample-data/README.md) contains reviewed fictional portfolios
  and strategies.
- [`stocks-by-ticker/`](stocks-by-ticker/README.md) contains generated historical
  price partitions; only its documentation and ignore rules are source files.
- `pyproject.toml`, `mise.toml`, and `uv.lock` define the development environment,
  tasks, dependencies, and locked package versions.

## Documentation

See the [documentation index](docs/README.md) for the domain model, storage
contract, data-root setup, schemas, input formats, CLI guides, design decisions,
planned work, and sources.

## Per-user configuration

See [Sample and Personal Data](docs/data.md) for the private-storage boundary and
per-user setup. The [CLI configuration reference](docs/cli.md#data-root)
documents path resolution.

## Development

Before committing, run:

```shell
mise run format
mise run lint
mise run mypy
mise run tests
mise run build
```
