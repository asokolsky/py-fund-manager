# Tests

The test suite uses Python's standard-library `unittest` framework. Tests are
deterministic: Yahoo Finance calls are mocked, and temporary directories contain
files created during each test.

## Run the suite

From the repo root, run every test through the project environment:

```shell
mise run tests
```

The task executes all `tests/*_test.py` modules in verbose mode.

## Run selected tests

Run one module:

```shell
mise exec -- uv run -m unittest -v tests.config_test
```

Run one test class or method by its dotted name:

```shell
mise exec -- uv run -m unittest -v \
  tests.portfolio_test.TestPortfolioStorage.test_load_portfolio
```

## Test modules

- `config_test.py` covers per-user TOML configuration and data-root resolution.
- `download_test.py` covers ticker and year parsing, downloads, normalization,
  concurrency, and Parquet storage.
- `log_test.py` covers logging configuration.
- `main_test.py` covers CLI parsing and dispatch.
- `portfolio_test.py` covers portfolio, transaction, strategy, and import
  validation.
- `rebalance_test.py` covers derived holdings, exact order and cash arithmetic,
  cached-price availability and provenance, deterministic inputs, and strict
  contribution and withdrawal plans.
- `playground_test.py` opens a fictional portfolio with USD 100,000, executes and
  persists its first equal-weight Mag7 rebalance, imports a dividend, and executes
  a second rebalance through the generic broker
  contract at deterministic historical prices.
- `strategy_test.py` covers immutable strategy revisions and effective assignment
  history.
- `validation_test.py` covers complete side-effect-free data-root validation and
  aggregated resource errors.

Regular tests must not make live Yahoo Finance requests. Explicit end-to-end
checks should write into a temporary directory and remove their downloaded data
after validation.
