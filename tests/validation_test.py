"""Tests for complete, side-effect-free data-root manifest validation."""

import tempfile
import unittest
from pathlib import Path

from py_fund_manager.validation import DataValidationError, validate_data_root


class TestDataValidation(unittest.TestCase):
    """Verify complete resource discovery and aggregate validation failures."""

    def test_committed_sample_data_is_valid_without_writes(self) -> None:
        """Validate every sample resource without changing file metadata."""
        root = Path(__file__).parents[1] / 'sample-data'
        before = {
            path: path.stat().st_mtime_ns for path in root.rglob('*') if path.is_file()
        }

        summary = validate_data_root(root)

        after = {
            path: path.stat().st_mtime_ns for path in root.rglob('*') if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(summary.portfolios, 1)
        self.assertEqual(summary.strategies, 2)
        self.assertEqual(summary.strategy_histories, 1)
        self.assertEqual(summary.revisions, 1)

    def test_validator_reports_independent_resource_errors(self) -> None:
        """Aggregate failures from Portfolio and Strategy discovery."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'portfolio/broken').mkdir(parents=True)
            (root / 'strategy/broken').mkdir(parents=True)

            with self.assertRaises(DataValidationError) as context:
                validate_data_root(root)

        self.assertEqual(len(context.exception.errors), 2)
        self.assertIn('no Portfolio manifest', context.exception.errors[0])
        self.assertIn('no Strategy manifest', context.exception.errors[1])


if __name__ == '__main__':
    unittest.main()
