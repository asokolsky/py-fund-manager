# Command-Line Interface

Run the CLI through the repo's `mise` environment:

```shell
mise run py-fund-manager -- COMMAND [OPTIONS]
```

Available commands:

- [`portfolio`](cli-portfolio.md) creates portfolio metadata and can bootstrap
  opening positions.
- [`download`](cli-download.md) downloads historical market prices.

Use `mise run py-fund-manager -- --help` for the current command list.

## Data root

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

See the [sample-data guide](../sample-data/README.md) for the expected private
checkout layout.
