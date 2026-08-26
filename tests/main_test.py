"""Tests for the py_fund_manager command-line entry point."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from unittest.mock import patch

from py_fund_manager import __main__ as cli
from py_fund_manager.download import Interval
from py_fund_manager.schemas import StrategyAssignment, StrategyRevisionReference


class TestCLI(unittest.TestCase):
    """Verify top-level CLI parsing and command dispatch."""

    def test_data_directory_uses_global_configuration(self) -> None:
        """Use the data root selected by per-user configuration."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = cli.Path(temporary_directory)
            with patch.object(cli, 'configured_data_root', return_value=data_directory):
                self.assertEqual(cli.data_directory(), data_directory)

    def test_portfolio_command_fails_without_configuration(self) -> None:
        """Fail a portfolio command when the global setting is absent."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            '--create',
            'etrade-alex-roth-ira',
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(
                cli,
                'data_directory',
                side_effect=cli.ConfigurationError('configuration is required'),
            ),
            patch.object(cli, 'create_portfolio') as create_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 1)
        create_mock.assert_not_called()

    def test_version_writes_to_stdout(self) -> None:
        """Print the exact package version to stdout and exit successfully."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, 'argv', [cli.CLI_NAME, '--version']),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), f'{cli.__version__}\n')
        self.assertEqual(stderr.getvalue(), '')

    def test_main_passes_parsed_download_arguments(self) -> None:
        """Pass parsed ticker, year, and interval values to the downloader."""
        arguments = [
            cli.CLI_NAME,
            'download',
            '2025',
            '--tickers=AAPL,MSFT',
            '--interval=1h',
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'download', return_value=0) as download_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        download_mock.assert_called_once_with(
            {'AAPL', 'MSFT'}, (2025, 2025), Interval.HOURLY
        )

    def test_create_portfolio_and_import_stocks(self) -> None:
        """Create a portfolio before importing its opening positions."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            '--create',
            'etrade-alex-roth-ira',
            'import-stocks',
            'stocks.csv',
        ]
        data_directory = cli.Path('test-data')
        portfolio_directory = data_directory / 'portfolios' / 'etrade-alex-roth-ira'
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'create_portfolio', return_value=portfolio_directory
            ) as create_mock,
            patch.object(
                cli, 'import_opening_positions', return_value=2
            ) as import_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        create_mock.assert_called_once_with(data_directory, 'etrade-alex-roth-ira')
        import_mock.assert_called_once_with(portfolio_directory, cli.Path('stocks.csv'))

    def test_set_portfolio_strategy(self) -> None:
        """Parse and dispatch an effective-dated strategy assignment."""
        effective_at = datetime(2026, 9, 1, tzinfo=UTC)
        assignment = StrategyAssignment(
            id='assignment-test',
            effective_at=effective_at,
            strategy=StrategyRevisionReference(
                id='SnP500-direct', revision=f'sha256:{"a" * 64}'
            ),
            reason='Adopt direct replication',
        )
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'etrade-alex-roth-ira',
            'strategy',
            'set',
            'SnP500-direct',
            '--effective-at',
            '2026-09-01T00:00:00Z',
            '--reason',
            'Adopt direct replication',
        ]
        data_directory = cli.Path('test-data')
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'assign_strategy', return_value=assignment
            ) as assign_mock,
            redirect_stdout(io.StringIO()),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        assign_mock.assert_called_once_with(
            data_directory,
            'etrade-alex-roth-ira',
            'SnP500-direct',
            effective_at,
            'Adopt direct replication',
        )


if __name__ == '__main__':
    unittest.main()
