"""Load per-user configuration for py-fund-manager."""

import os
import tomllib
from pathlib import Path
from typing import Any

APPLICATION_DIRECTORY = 'py-fund-manager'
CONFIG_FILENAME = 'config.toml'


class ConfigurationError(ValueError):
    """Report invalid application configuration."""


def config_file_path() -> Path:
    """Return the per-user TOML configuration path."""
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    config_home = (
        Path(xdg_config_home).expanduser()
        if xdg_config_home
        else Path.home() / '.config'
    )
    return config_home / APPLICATION_DIRECTORY / CONFIG_FILENAME


def configured_data_root(path: Path | None = None) -> Path:
    """Return the required data root from per-user configuration."""
    config_path = path or config_file_path()
    if not config_path.exists():
        raise ConfigurationError(
            f"Configuration '{config_path}' does not exist; copy the sample "
            'from docs/config.toml.example'
        )

    try:
        with config_path.open('rb') as config_file:
            document: dict[str, Any] = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            f"Cannot read configuration '{config_path}': {error}"
        ) from error

    data = document.get('data')
    if not isinstance(data, dict) or 'root' not in data:
        raise ConfigurationError(
            f"Configuration '{config_path}' requires a [data] root setting"
        )

    root = data['root']
    if not isinstance(root, str) or not root.strip():
        raise ConfigurationError(
            f"Configuration '{config_path}' requires data.root to be a path string"
        )

    configured_path = Path(root).expanduser()
    if not configured_path.is_absolute():
        configured_path = config_path.parent / configured_path
    return configured_path.resolve()
