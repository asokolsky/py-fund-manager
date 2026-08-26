"""Tests for immutable strategy revisions and effective assignment history."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from py_fund_manager.portfolio import create_portfolio
from py_fund_manager.schemas import Strategy, StrategyHistory
from py_fund_manager.strategy import (
    assign_strategy,
    effective_assignment,
    load_strategy_history,
    load_strategy_revision,
    strategy_revision,
)

STRATEGY_DOCUMENT = """schema_version: 1
id: balanced
name: Balanced
benchmark: $SPX
allocation:
  type: target_weights
  positions:
    AAPL: "0.600000"
    MSFT: "0.400000"
"""


class TestStrategyHistory(unittest.TestCase):
    """Verify strategy revisions and append-only effective assignments."""

    def test_history_requires_unique_ordered_assignments(self) -> None:
        """Reject duplicate identities and non-increasing effective times."""
        reference = {
            'id': 'balanced',
            'revision': f'sha256:{"a" * 64}',
        }
        with self.assertRaisesRegex(ValidationError, 'increasing effective times'):
            StrategyHistory.model_validate(
                {
                    'assignments': [
                        {
                            'id': 'second',
                            'effective_at': '2026-02-01T00:00:00Z',
                            'strategy': reference,
                        },
                        {
                            'id': 'first',
                            'effective_at': '2026-01-01T00:00:00Z',
                            'strategy': reference,
                        },
                    ]
                }
            )
        with self.assertRaisesRegex(ValidationError, 'IDs must be unique'):
            StrategyHistory.model_validate(
                {
                    'assignments': [
                        {
                            'id': 'duplicate',
                            'effective_at': '2026-01-01T00:00:00Z',
                            'strategy': reference,
                        },
                        {
                            'id': 'duplicate',
                            'effective_at': '2026-02-01T00:00:00Z',
                            'strategy': reference,
                        },
                    ]
                }
            )

    def test_history_requires_aware_effective_time(self) -> None:
        """Reject an assignment whose effective time has no UTC offset."""
        with self.assertRaisesRegex(ValidationError, 'must include a UTC offset'):
            StrategyHistory.model_validate(
                {
                    'assignments': [
                        {
                            'id': 'initial',
                            'effective_at': '2026-01-01T00:00:00',
                            'strategy': {
                                'id': 'balanced',
                                'revision': f'sha256:{"a" * 64}',
                            },
                        }
                    ]
                }
            )

    def test_revision_ignores_yaml_formatting(self) -> None:
        """Hash validated content instead of source YAML formatting."""
        first = Strategy.model_validate(yaml.safe_load(STRATEGY_DOCUMENT))
        reordered = Strategy.model_validate(
            yaml.safe_load(
                """name: Balanced
allocation: {positions: {MSFT: "0.400000", AAPL: "0.600000"}, type: target_weights}
id: balanced
benchmark: $SPX
schema_version: 1
"""
            )
        )

        self.assertEqual(strategy_revision(first), strategy_revision(reordered))

    def test_assign_and_resolve_strategy_revision(self) -> None:
        """Append assignments and resolve the one effective at a requested time."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_portfolio(root, 'example-account')
            strategy_directory = root / 'strategies' / 'balanced'
            strategy_directory.mkdir(parents=True)
            (strategy_directory / 'strategy.yaml').write_text(
                STRATEGY_DOCUMENT, encoding='utf-8'
            )
            first = assign_strategy(
                root,
                'example-account',
                'balanced',
                datetime(2026, 1, 1, tzinfo=UTC),
                'Initial strategy',
            )
            second = assign_strategy(
                root,
                'example-account',
                'balanced',
                datetime(2026, 2, 1, tzinfo=UTC),
                'Refresh allocation',
            )

            history = load_strategy_history(
                root / 'portfolios/example-account/strategy-history.yaml'
            )
            selected = effective_assignment(history, datetime(2026, 1, 15, tzinfo=UTC))
            strategy = load_strategy_revision(root, selected.strategy)

        self.assertEqual(selected.id, first.id)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(strategy.id, 'balanced')
        self.assertEqual(len(history.assignments), 2)

    def test_assignment_rejects_an_earlier_effective_time(self) -> None:
        """Preserve chronological history when appending an assignment."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_portfolio(root, 'example-account')
            strategy_directory = root / 'strategies' / 'balanced'
            strategy_directory.mkdir(parents=True)
            (strategy_directory / 'strategy.yaml').write_text(
                STRATEGY_DOCUMENT, encoding='utf-8'
            )
            assign_strategy(
                root,
                'example-account',
                'balanced',
                datetime(2026, 2, 1, tzinfo=UTC),
            )

            with self.assertRaisesRegex(ValueError, 'increasing effective times'):
                assign_strategy(
                    root,
                    'example-account',
                    'balanced',
                    datetime(2026, 1, 1, tzinfo=UTC),
                )

    def test_sample_history_resolves_immutable_strategy(self) -> None:
        """Keep the fictional sample aligned with the implemented contract."""
        root = Path(__file__).parents[1] / 'sample-data'
        history = load_strategy_history(
            root / 'portfolios/sample/strategy-history.yaml'
        )
        assignment = effective_assignment(history, datetime(2026, 1, 1, tzinfo=UTC))
        strategy = load_strategy_revision(root, assignment.strategy)

        self.assertEqual(assignment.strategy.id, 'two-stock-example')
        self.assertEqual(strategy.id, 'two-stock-example')


if __name__ == '__main__':
    unittest.main()
