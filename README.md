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
- Simulate reviewed rebalance plans deterministically with configurable broker
  quantity precision and exchange-compatible historical execution prices.
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

## CLI configuration

Portfolio commands require a per-user configuration file at
`~/.config/py-fund-manager/config.toml`. Copy
[`docs/config.toml.example`](docs/config.toml.example), then select the portfolio
and strategy data root with `data.root`. See [Sample and Personal
Data](docs/data.md#data-root-configuration) for installation, path-resolution,
sample-data, and private-storage guidance.

## Development

Before committing, run:

```shell
mise run format
mise run lint
mise run mypy
mise run tests
mise run build
```
