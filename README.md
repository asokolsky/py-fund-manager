# py-fund-manager

`py-fund-manager` is a Python library and CLI for maintaining portfolio data and
downloading historical market prices.

## Current capabilities

- Create a portfolio with validated YAML metadata.
- Bootstrap opening positions from a canonical holdings CSV.
- Store positions in a validated, append-oriented transaction ledger.
- Assign effective-dated strategies with immutable content revisions.
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

- [Concepts](docs/concepts.md) defines the domain model.
- [Portfolio storage and validation](docs/README.md) is the data contract.
- [Design decisions](docs/design-decisions.md) records settled choices.
- [Planned work](docs/todo.md) tracks unimplemented capabilities and open work.
- [Sample data](sample-data/README.md) explains fictional examples and private
  data roots.
- [Command-line interface](docs/cli.md) links command-specific usage guides.
- [Sources](docs/sources.md) lists external references.

## Per-user configuration

[`docs/config.toml.example`](docs/config.toml.example) is a sample global
configuration. Copy it to `~/.config/py-fund-manager/config.toml` and update
`data.root` to select the directory containing private `portfolios/` and
`strategies/`. The setting is required; the supplied sample explicitly selects
this repo's fictional `sample-data/` root. See the [CLI configuration
reference](docs/cli.md#data-root) for installation and path-resolution details.

## Development

Before committing, run:

```shell
mise run format
mise run lint
mise run mypy
mise run tests
mise run build
```
