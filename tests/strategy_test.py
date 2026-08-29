"""Tests for immutable strategy revisions and effective assignment history."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from py_fund_manager.portfolio import create_portfolio
from py_fund_manager.schemas import (
    ObjectMetadata,
    Strategy,
    StrategyHistory,
    StrategyRevisionReference,
)
from py_fund_manager.strategy import (
    analyze_strategy,
    assign_strategy,
    effective_assignment,
    load_strategy_history,
    load_strategy_revision,
    strategy_revision,
    strategy_tickers,
)

STRATEGY_DOCUMENT = """apiVersion: v1
kind: Strategy
metadata:
  name: balanced
  display_name: Balanced
spec:
  benchmark: $SPX
  allocation:
    type: target_weights
    positions:
      AAPL: "0.600000"
      MSFT: "0.400000"
"""


class TestStrategyHistory(unittest.TestCase):
    """Verify strategy revisions and append-only effective assignments."""

    def test_analyze_strategy_and_extract_tickers(self) -> None:
        """Summarize validated allocation details and sort its ticker symbols."""
        strategy = Strategy.model_validate(yaml.safe_load(STRATEGY_DOCUMENT))

        self.assertEqual(
            analyze_strategy(strategy).model_dump(mode='json'),
            {
                'name': 'balanced',
                'display_name': 'Balanced',
                'benchmark': '$SPX',
                'allocation_type': 'target_weights',
                'position_count': 2,
                'total_weight': '1.000000',
            },
        )
        self.assertEqual(strategy_tickers(strategy), ('AAPL', 'MSFT'))

    def test_history_requires_unique_ordered_assignments(self) -> None:
        """Reject duplicate identities and non-increasing effective times."""
        reference = {
            'name': 'balanced',
            'revision': f'sha256:{"a" * 64}',
        }
        with self.assertRaisesRegex(ValidationError, 'increasing effective times'):
            StrategyHistory.model_validate(
                {
                    'apiVersion': 'v1',
                    'kind': 'StrategyHistory',
                    'metadata': {'name': 'example-account'},
                    'spec': {
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
                    },
                }
            )
        with self.assertRaisesRegex(ValidationError, 'IDs must be unique'):
            StrategyHistory.model_validate(
                {
                    'apiVersion': 'v1',
                    'kind': 'StrategyHistory',
                    'metadata': {'name': 'example-account'},
                    'spec': {
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
                    },
                }
            )

    def test_history_requires_aware_effective_time(self) -> None:
        """Reject an assignment whose effective time has no UTC offset."""
        with self.assertRaisesRegex(ValidationError, 'must include a UTC offset'):
            StrategyHistory.model_validate(
                {
                    'apiVersion': 'v1',
                    'kind': 'StrategyHistory',
                    'metadata': {'name': 'example-account'},
                    'spec': {
                        'assignments': [
                            {
                                'id': 'initial',
                                'effective_at': '2026-01-01T00:00:00',
                                'strategy': {
                                    'name': 'balanced',
                                    'revision': f'sha256:{"a" * 64}',
                                },
                            }
                        ]
                    },
                }
            )

    def test_revision_ignores_yaml_formatting(self) -> None:
        """Hash validated content instead of source YAML formatting."""
        first = Strategy.model_validate(yaml.safe_load(STRATEGY_DOCUMENT))
        reordered = Strategy.model_validate(
            yaml.safe_load(
                """kind: Strategy
spec:
  allocation: {positions: {MSFT: "0.400000", AAPL: "0.600000"}, type: target_weights}
  benchmark: $SPX
metadata: {display_name: Balanced, name: balanced}
apiVersion: v1
"""
            )
        )

        self.assertEqual(strategy_revision(first), strategy_revision(reordered))

    def test_assign_and_resolve_strategy_revision(self) -> None:
        """Append assignments and resolve the one effective at a requested time."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_portfolio(
                root, 'example-account', broker='example', account_id='account'
            )
            strategy_directory = root / 'strategy' / 'balanced'
            strategy_directory.mkdir(parents=True)
            (strategy_directory / 'strategy.yaml').write_text(
                STRATEGY_DOCUMENT, encoding='utf-8'
            )
            (strategy_directory / 'strategy.yaml').rename(
                strategy_directory / 'allocation.yaml'
            )
            first = assign_strategy(
                root,
                'example-account',
                'balanced',
                datetime(2026, 1, 1, tzinfo=UTC),
                'Initial strategy',
            )
            history_path = root / 'portfolio/example-account/strategy-history.yaml'
            renamed_history = root / 'portfolio/example-account/allocations.yaml'
            history_path.rename(renamed_history)
            second = assign_strategy(
                root,
                'example-account',
                'balanced',
                datetime(2026, 2, 1, tzinfo=UTC),
                'Refresh allocation',
            )

            history = load_strategy_history(renamed_history)
            selected = effective_assignment(history, datetime(2026, 1, 15, tzinfo=UTC))
            strategy = load_strategy_revision(root, selected.strategy)

        self.assertEqual(selected.id, first.id)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(strategy.metadata.name, 'balanced')
        self.assertEqual(len(history.spec.assignments), 2)

    def test_assignment_rejects_an_earlier_effective_time(self) -> None:
        """Reject chronology before creating a revision for changed content."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_portfolio(
                root, 'example-account', broker='example', account_id='account'
            )
            strategy_directory = root / 'strategy' / 'balanced'
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
            revisions = strategy_directory / 'revisions'
            before = {path.name for path in revisions.iterdir()}
            changed = STRATEGY_DOCUMENT.replace('"0.600000"', '"0.700000"').replace(
                '"0.400000"', '"0.300000"'
            )
            (strategy_directory / 'strategy.yaml').write_text(changed, encoding='utf-8')

            with self.assertRaisesRegex(
                ValueError,
                '2026-01-01T00:00:00.*later than.*2026-02-01T00:00:00',
            ):
                assign_strategy(
                    root,
                    'example-account',
                    'balanced',
                    datetime(2026, 1, 1, tzinfo=UTC),
                )

            self.assertEqual({path.name for path in revisions.iterdir()}, before)

    def test_resource_names_cannot_escape_their_directory(self) -> None:
        """Reject path separators while preserving mixed-case strategy names."""
        self.assertEqual(ObjectMetadata(name='SnP500-direct').name, 'SnP500-direct')
        with self.assertRaisesRegex(ValidationError, 'string_pattern_mismatch'):
            ObjectMetadata(name='../../../outside')
        with self.assertRaisesRegex(ValidationError, 'string_pattern_mismatch'):
            StrategyRevisionReference(name='../outside', revision=f'sha256:{"a" * 64}')

    def test_sample_history_resolves_immutable_strategy(self) -> None:
        """Keep the fictional sample aligned with the implemented contract."""
        root = Path(__file__).parents[1] / 'sample-data'
        history = load_strategy_history(root / 'portfolio/sample/strategy-history.yaml')
        assignment = effective_assignment(history, datetime(2026, 1, 1, tzinfo=UTC))
        strategy = load_strategy_revision(root, assignment.strategy)

        self.assertEqual(assignment.strategy.name, 'mag7')
        self.assertEqual(strategy.metadata.name, 'mag7')
        self.assertEqual(len(strategy.target_weights), 7)


if __name__ == '__main__':
    unittest.main()
