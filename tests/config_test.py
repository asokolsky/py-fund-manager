"""Tests for per-user TOML configuration."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from py_fund_manager.config import (
    ConfigurationError,
    config_file_path,
    configured_data_root,
)


class TestConfiguration(unittest.TestCase):
    """Verify configuration paths and data-root parsing."""

    def test_config_path_honors_xdg_config_home(self) -> None:
        """Place configuration below XDG_CONFIG_HOME when it is set."""
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.dict('os.environ', {'XDG_CONFIG_HOME': temporary_directory}),
        ):
            self.assertEqual(
                config_file_path(),
                Path(temporary_directory) / 'py-fund-manager/config.toml',
            )

    def test_missing_config_is_rejected(self) -> None:
        """Require an explicit per-user configuration file."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_path = Path(temporary_directory) / 'config.toml'
            with self.assertRaisesRegex(ConfigurationError, 'does not exist'):
                configured_data_root(missing_path)

    def test_missing_data_root_setting_is_rejected(self) -> None:
        """Require the configuration to select a data root."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / 'config.toml'
            config_path.write_text('[other]\nvalue = true\n')

            with self.assertRaisesRegex(ConfigurationError, r'\[data\] root'):
                configured_data_root(config_path)

    def test_relative_data_root_is_relative_to_config(self) -> None:
        """Resolve a relative data root from the configuration directory."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_directory = Path(temporary_directory)
            config_path = config_directory / 'config.toml'
            config_path.write_text('[data]\nroot = "../private-data"\n')

            self.assertEqual(
                configured_data_root(config_path),
                (config_directory / '../private-data').resolve(),
            )

    def test_invalid_data_root_type_is_rejected(self) -> None:
        """Reject non-string data-root values."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / 'config.toml'
            config_path.write_text('[data]\nroot = 42\n')

            with self.assertRaisesRegex(ConfigurationError, 'data.root'):
                configured_data_root(config_path)


if __name__ == '__main__':
    unittest.main()
