# Repository guidance

## Development workflow

- Use `mise` for routine project commands.
- Run the CLI with `mise py-fund-manager -- [arguments]`.
- After the final edit and before completing, committing, pushing, opening, or
  merging a pull request, run:
  - `mise run format`
  - `mise run lint`
  - `mise run mypy`
  - `mise run tests`
- Run `git diff --check` and inspect `git status` and the complete diff after
  formatting. Do not rely on an earlier run made before the final edit.
- Treat these local checks as the required verification. Do not substitute
  GitHub checks for running them in the task worktree.
- Build source and wheel distributions with `mise run build`.
- Do not commit or push unless explicitly requested.

## Naming

- Use `py_fund_manager` for the Python import package and module paths.
- Use `py-fund-manager` only for the distribution name and user-facing CLI name.

## Historical-price downloads

- Keep Yahoo Finance requests in `py_fund_manager/download.py` and CLI wiring in
  `py_fund_manager/__main__.py`.
- Preserve successful ticker and year files when another request fails.
- Download multiple tickers with a bounded worker pool.
- Request each year independently so later years are attempted after an earlier
  failure.
- Yahoo Finance hourly data is limited to approximately the most recent 730
  days.
- Keep blocking network calls out of unrelated application or UI threads.

## Data storage

- Treat contents below `stocks-by-ticker/` as generated data, except for
  `README.md` and `.gitignore`.
- Follow `stocks-by-ticker/README.md` for the partition layout, Parquet schema,
  metadata, and atomic-write requirements.
- Do not manually edit generated Parquet files.

## Tests and network access

- Keep regular tests deterministic and mock Yahoo Finance requests.
- Keep test fixtures isolated from mutable, generated, and ignored files in the
  working tree. Copy committed sample resources to a temporary directory before
  tests that inspect or modify a complete data root.
- Pin environment inputs that affect expected output inside the relevant test,
  including terminal width, locale, and timezone. A clean `git status` does not
  prove ignored runtime data is absent.
- Use live Yahoo requests only for explicit end-to-end validation and write
  validation output to a temporary directory.
- Remove temporary live-download data after validation.

## Generated files

- Do not commit `.venv`, Python caches, Ruff or mypy caches, `build`, `dist`, or
  `py_fund_manager.egg-info`.
- Use `mise run clean` for local cleanup.
