"""Tests for logging configuration."""

import logging
import sys
import unittest
from unittest.mock import patch

from py_fund_manager.log import setup_logging


class TestLogging(unittest.TestCase):
    """Verify command-line logging behavior."""

    def test_setup_logging_writes_to_stderr(self) -> None:
        """Configure a stderr stream handler instead of a log file."""
        with patch('py_fund_manager.log.logging.basicConfig') as basic_config:
            setup_logging('py_fund_manager.test', logging.INFO)

        basic_config.assert_called_once_with(
            stream=sys.stderr,
            datefmt='%H:%M:%S',
            format='{asctime} {name} {levelname} {message}',
            style='{',
            level=logging.INFO,
        )


if __name__ == '__main__':
    unittest.main()
