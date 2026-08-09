"""Tests for the py_fund_manager command-line entry point."""

import sys
import unittest
from unittest.mock import patch

from py_fund_manager import __main__ as cli
from py_fund_manager.download import Interval


class TestCLI(unittest.TestCase):
    """Verify top-level CLI parsing and command dispatch."""

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


if __name__ == '__main__':
    unittest.main()
