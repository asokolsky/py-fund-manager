"""Tests for complete, side-effect-free data-root manifest validation."""

import tempfile
import unittest
from pathlib import Path
from shutil import copytree
from unittest.mock import patch

from py_fund_manager.portfolio import load_manifest
from py_fund_manager.strategy import load_strategy
from py_fund_manager.validation import DataValidationError, validate_data_root


class TestDataValidation(unittest.TestCase):
    """Verify complete resource discovery and aggregate validation failures."""

    def _copy_stable_sample_data(self, destination: Path) -> Path:
        """Copy only the sample resources used as stable test fixtures."""
        source = Path(__file__).parents[1] / 'sample-data'
        root = destination / 'sample-data'
        resources = (
            Path('portfolio/sample'),
            Path('strategy/SnP500-direct'),
            Path('strategy/mag7'),
        )
        for resource in resources:
            target = root / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            copytree(source / resource, target)
        return root

    def test_committed_sample_data_is_valid_without_writes(self) -> None:
        """Validate every sample resource without changing file metadata."""
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_stable_sample_data(Path(directory))
            before = {
                path: path.stat().st_mtime_ns
                for path in root.rglob('*')
                if path.is_file()
            }

            summary = validate_data_root(root)

            after = {
                path: path.stat().st_mtime_ns
                for path in root.rglob('*')
                if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual(summary.portfolios, 1)
            self.assertEqual(summary.strategies, 2)
            self.assertEqual(summary.strategy_histories, 1)
            self.assertEqual(summary.revisions, 1)

    def test_validator_parses_each_sample_manifest_once(self) -> None:
        """Reuse discovered models and referenced revision validation results."""
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_stable_sample_data(Path(directory))
            with (
                patch(
                    'py_fund_manager.validation.load_manifest', wraps=load_manifest
                ) as manifest_mock,
                patch(
                    'py_fund_manager.strategy.load_strategy', wraps=load_strategy
                ) as strategy_mock,
            ):
                validate_data_root(root)

        current_paths = [call.args[0] for call in manifest_mock.call_args_list]
        revision_paths = [call.args[0] for call in strategy_mock.call_args_list]
        self.assertEqual(len(current_paths), len(set(current_paths)))
        self.assertEqual(len(revision_paths), len(set(revision_paths)))

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

    def test_readme_does_not_disable_resource_validation(self) -> None:
        """Require an explicit marker before skipping documentation directories."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio = root / 'portfolio/oops'
            portfolio.mkdir(parents=True)
            (portfolio / 'README.md').write_text('# Not configured yet\n')
            (root / 'strategy').mkdir()

            with self.assertRaisesRegex(DataValidationError, 'no Portfolio manifest'):
                validate_data_root(root)

    def test_validator_reports_independent_errors_in_one_portfolio(self) -> None:
        """Continue checking a ledger and history after Portfolio identity fails."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio = root / 'portfolio/sample'
            portfolio.mkdir(parents=True)
            (root / 'strategy').mkdir()
            (portfolio / 'portfolio.yaml').write_text(
                """apiVersion: v1
kind: Portfolio
metadata: {name: wrong, display_name: Wrong}
spec: {broker: example, account_id: sample, base_currency: USD}
""",
                encoding='utf-8',
            )
            (portfolio / 'transactions.csv').write_text(
                'id,occurred_at\nbroken,not-a-timestamp\n', encoding='utf-8'
            )
            (portfolio / 'history.yaml').write_text(
                f"""apiVersion: v1
kind: StrategyHistory
metadata: {{name: sample}}
spec:
  assignments:
    - id: missing-revision
      effective_at: 2026-01-01T00:00:00Z
      strategy:
        name: missing
        revision: sha256:{'a' * 64}
    - id: another-missing-revision
      effective_at: 2026-02-01T00:00:00Z
      strategy:
        name: also-missing
        revision: sha256:{'b' * 64}
""",
                encoding='utf-8',
            )

            with self.assertRaises(DataValidationError) as context:
                validate_data_root(root)

        messages = '\n'.join(context.exception.errors)
        self.assertGreaterEqual(len(context.exception.errors), 4)
        self.assertIn("expected metadata.name 'sample', got 'wrong'", messages)
        self.assertIn('transactions.csv', messages)
        self.assertIn("history.yaml: assignment 'missing-revision'", messages)
        self.assertIn("history.yaml: assignment 'another-missing-revision'", messages)
        self.assertIn('strategy/missing/revisions', messages)
        self.assertIn('strategy/also-missing/revisions', messages)


if __name__ == '__main__':
    unittest.main()
