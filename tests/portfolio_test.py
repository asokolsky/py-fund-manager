"""Tests for portfolio, transaction, and strategy storage models."""

import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from py_fund_manager.portfolio import (
    create_portfolio,
    find_manifest,
    import_activity,
    import_opening_snapshot,
    initialize_opening_balances,
    load_portfolio,
    load_strategy,
    load_transactions,
)
from py_fund_manager.schemas import Portfolio, Transaction, TransactionType


class TestPortfolioStorage(unittest.TestCase):
    """Verify loading and validation of the documented storage formats."""

    def test_load_portfolio(self) -> None:
        """Load account configuration and normalize its currency."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'portfolio.yaml'
            path.write_text(
                """apiVersion: v1
kind: Portfolio
metadata:
  name: etrade-roth-ira
  display_name: E*TRADE Roth IRA
spec:
  broker: etrade
  account_id: "...1234"
  base_currency: usd
""",
                encoding='utf-8',
            )
            portfolio = load_portfolio(path)

        self.assertEqual(portfolio.metadata.name, 'etrade-roth-ira')
        self.assertEqual(portfolio.spec.base_currency, 'USD')

    def test_load_strategy_requires_weights_to_total_one(self) -> None:
        """Accept precise weights and reject an incomplete allocation."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'strategy.yaml'
            path.write_text(
                """apiVersion: v1
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
""",
                encoding='utf-8',
            )
            strategy = load_strategy(path)
            self.assertEqual(strategy.target_weights['AAPL'], Decimal('0.600000'))

            path.write_text(path.read_text().replace('0.400000', '0.300000'))
            with self.assertRaisesRegex(ValueError, 'weights must total 1.0'):
                load_strategy(path)

    def test_yaml_loader_rejects_duplicate_nested_keys(self) -> None:
        """Reject ambiguous YAML keys and report the source path."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'account.yaml'
            path.write_text(
                """apiVersion: v1
kind: Portfolio
metadata:
  name: sample
  display_name: First
  display_name: Second
spec:
  broker: example
  account_id: sample
  base_currency: USD
""",
                encoding='utf-8',
            )
            with self.assertRaises(ValueError) as context:
                load_portfolio(path)

        self.assertIn('account.yaml', str(context.exception))
        self.assertIn("duplicate key 'display_name'", str(context.exception))

    def test_yaml_loader_rejects_multiple_documents(self) -> None:
        """Keep canonical storage to exactly one manifest per YAML file."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resources.yaml'
            path.write_text(
                """apiVersion: v1
kind: Portfolio
metadata: {name: first, display_name: First}
spec: {broker: example, account_id: first, base_currency: USD}
---
apiVersion: v1
kind: Portfolio
metadata: {name: second, display_name: Second}
spec: {broker: example, account_id: second, base_currency: USD}
""",
                encoding='utf-8',
            )
            with self.assertRaisesRegex(ValueError, 'expected a single document'):
                load_portfolio(path)

    def test_manifest_discovery_does_not_depend_on_filename(self) -> None:
        """Resolve a Portfolio by kind after its conventional file is renamed."""
        with tempfile.TemporaryDirectory() as directory:
            portfolio_directory = create_portfolio(
                Path(directory), 'sample', broker='example', account_id='sample'
            )
            original = portfolio_directory / 'portfolio.yaml'
            renamed = portfolio_directory / 'account-details.yaml'
            original.rename(renamed)

            path, manifest = find_manifest(
                portfolio_directory, 'Portfolio', expected_name='sample'
            )

        self.assertEqual(path.name, 'account-details.yaml')
        self.assertEqual(manifest.metadata.name, 'sample')

    def test_manifest_dispatch_rejects_legacy_and_invalid_envelopes(self) -> None:
        """Require the exact v1 API and Portfolio kind discriminator."""
        valid = """apiVersion: v1
kind: Portfolio
metadata: {name: sample, display_name: Sample}
spec: {broker: example, account_id: sample, base_currency: USD}
"""
        invalid_documents = {
            'numeric API version': valid.replace('apiVersion: v1', 'apiVersion: 1'),
            'wrong kind': valid.replace('kind: Portfolio', 'kind: Strategy'),
            'legacy version': valid.replace('apiVersion: v1', 'schema_version: 1'),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'account.yaml'
            for label, document in invalid_documents.items():
                with self.subTest(label=label):
                    path.write_text(document, encoding='utf-8')
                    with self.assertRaises((TypeError, ValueError)):
                        load_portfolio(path)

    def test_manifest_discovery_rejects_duplicate_kinds(self) -> None:
        """Reject two current Portfolio manifests in one resource directory."""
        with tempfile.TemporaryDirectory() as directory:
            portfolio_directory = create_portfolio(
                Path(directory), 'sample', broker='example', account_id='sample'
            )
            duplicate = portfolio_directory / 'duplicate.yaml'
            duplicate.write_text(
                (portfolio_directory / 'portfolio.yaml').read_text(),
                encoding='utf-8',
            )

            with self.assertRaisesRegex(ValueError, 'multiple Portfolio manifests'):
                find_manifest(portfolio_directory, 'Portfolio')

    def test_generated_sp500_strategy_is_complete(self) -> None:
        """Validate the committed direct-replication allocation."""
        strategy_path = (
            Path(__file__).parents[1]
            / 'sample-data/strategy/SnP500-direct/strategy.yaml'
        )
        strategy = load_strategy(strategy_path)

        self.assertEqual(strategy.metadata.name, 'SnP500-direct')
        self.assertEqual(len(strategy.target_weights), 503)
        self.assertEqual(sum(strategy.target_weights.values()), Decimal(1))
        self.assertNotIn('SPY', strategy.target_weights)
        self.assertNotIn('2602335D', strategy.target_weights)

    def test_load_transactions_supports_opening_positions(self) -> None:
        """Load a broker baseline without losing exact cost-basis values."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'transactions.csv'
            path.write_text(
                """id,occurred_at,type,ticker,quantity,price,amount,cost_basis,currency,fees,external_id
open-001,2026-08-01T00:00:00-04:00,opening_position,aapl,12,,,2100.00,usd,,snapshot-1
tx-001,2026-08-20T14:32:00-04:00,buy,AAPL,10,225.15,,,USD,1.00,broker-1
""",
                encoding='utf-8',
            )
            transactions = load_transactions(path)

        self.assertEqual(transactions[0].type, TransactionType.OPENING_POSITION)
        self.assertEqual(transactions[0].ticker, 'AAPL')
        self.assertEqual(transactions[0].cost_basis, Decimal('2100.00'))
        self.assertEqual(transactions[1].price, Decimal('225.15'))

    def test_load_transactions_rejects_duplicate_ids(self) -> None:
        """Reject ambiguous ledger rows that reuse a transaction identity."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'transactions.csv'
            path.write_text(
                """id,occurred_at,type,ticker,quantity,price,amount,cost_basis,currency,fees,external_id
tx-001,2026-08-20T14:32:00+00:00,buy,AAPL,1,1,,,USD,,
tx-001,2026-08-21T14:32:00+00:00,sell,AAPL,1,2,,,USD,,
""",
                encoding='utf-8',
            )
            with self.assertRaisesRegex(ValueError, 'duplicate transaction id'):
                load_transactions(path)

    def test_load_transactions_requires_chronology_and_unique_external_ids(
        self,
    ) -> None:
        """Reject reordered facts and repeated broker identities."""
        header = 'id,occurred_at,type,ticker,quantity,price,amount,cost_basis,currency,fees,external_id\n'
        invalid_ledgers = {
            'chronology': (
                'tx-001,2026-08-21T14:32:00Z,deposit,,,,10,,USD,,broker-1\n'
                'tx-002,2026-08-20T14:32:00Z,deposit,,,,10,,USD,,broker-2\n'
            ),
            'external identity': (
                'tx-001,2026-08-20T14:32:00Z,deposit,,,,10,,USD,,broker-1\n'
                'tx-002,2026-08-21T14:32:00Z,deposit,,,,10,,USD,,broker-1\n'
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'transactions.csv'
            for label, rows in invalid_ledgers.items():
                with self.subTest(label=label):
                    path.write_text(header + rows, encoding='utf-8')
                    with self.assertRaisesRegex(
                        ValueError, 'occurs before|duplicate external_id'
                    ):
                        load_transactions(path)

    def test_pydantic_rejects_unknown_portfolio_fields(self) -> None:
        """Reject misspelled or unsupported persisted portfolio properties."""
        with self.assertRaises(ValidationError):
            Portfolio.model_validate(
                {
                    'apiVersion': 'v1',
                    'kind': 'Portfolio',
                    'metadata': {
                        'name': 'etrade-roth-ira',
                        'display_name': 'Roth IRA',
                    },
                    'spec': {
                        'broker': 'etrade',
                        'account_id': 'local-account',
                        'base_currency': 'USD',
                        'curreny': 'USD',
                    },
                }
            )

    def test_portfolio_rejects_strategy_pointer(self) -> None:
        """Keep effective strategy selection solely in strategy history."""
        with self.assertRaises(ValidationError):
            Portfolio.model_validate(
                {
                    'apiVersion': 'v1',
                    'kind': 'Portfolio',
                    'metadata': {
                        'name': 'etrade-roth-ira',
                        'display_name': 'Roth IRA',
                    },
                    'spec': {
                        'broker': 'etrade',
                        'account_id': 'local-account',
                        'base_currency': 'USD',
                        'strategy': 'SnP500-direct',
                    },
                }
            )

    def test_pydantic_rejects_invalid_transaction_shape(self) -> None:
        """Require aware timestamps and security fields for a purchase."""
        with self.assertRaises(ValidationError):
            Transaction.model_validate(
                {
                    'id': 'tx-001',
                    'occurred_at': '2026-08-26T12:00:00',
                    'type': 'buy',
                    'currency': 'USD',
                }
            )

    def test_create_portfolio(self) -> None:
        """Create loadable account configuration in the portfolio hierarchy."""
        with tempfile.TemporaryDirectory() as directory:
            portfolio_directory = create_portfolio(
                Path(directory),
                'brokerage',
                broker='historical',
                account_id='brokerage-123',
            )
            portfolio = load_portfolio(portfolio_directory / 'portfolio.yaml')

        self.assertEqual(portfolio.metadata.name, 'brokerage')
        self.assertEqual(portfolio.spec.broker, 'historical')
        self.assertEqual(portfolio.spec.account_id, 'brokerage-123')

    def test_import_opening_snapshot_preserves_source_and_writes_ledger(
        self,
    ) -> None:
        """Convert canonical opening facts into a loadable ledger."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'opening.csv'
            source.write_text(
                'asset,quantity,amount,cost_basis\n'
                'USD,,100000.00,\n'
                'AAPL,12.5,,2100.00\n',
                encoding='utf-8',
            )
            portfolio_directory = create_portfolio(
                root,
                'etrade-roth-ira',
                broker='etrade',
                account_id='roth-ira',
            )

            count = import_opening_snapshot(portfolio_directory, source)
            transactions = load_transactions(portfolio_directory / 'transactions.csv')

            self.assertEqual(count, 2)
            self.assertEqual(transactions[0].amount, Decimal('100000.00'))
            self.assertEqual(transactions[0].currency, 'USD')
            self.assertIsNone(transactions[0].external_id)
            self.assertEqual(transactions[1].quantity, Decimal('12.5'))
            self.assertEqual(transactions[1].cost_basis, Decimal('2100.00'))
            self.assertIsNone(transactions[1].external_id)
            self.assertEqual(
                (portfolio_directory / 'imports' / 'opening.csv').read_text(),
                source.read_text(),
            )

    def test_import_opening_snapshot_supports_cash_only_portfolio(self) -> None:
        """Bootstrap only cash at one explicit historical boundary."""
        statement_time = datetime(2020, 1, 2, 16, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'opening.csv'
            source.write_text(
                'asset,amount\nUSD,100000.00\n',
                encoding='utf-8',
            )
            portfolio_directory = create_portfolio(
                root,
                'playground',
                broker='historical',
                account_id='playground',
            )

            count = import_opening_snapshot(
                portfolio_directory,
                source,
                occurred_at=statement_time,
            )
            transactions = load_transactions(portfolio_directory / 'transactions.csv')

        self.assertEqual(count, 1)
        self.assertEqual(transactions[0].type, TransactionType.OPENING_CASH)
        self.assertEqual(transactions[0].amount, Decimal('100000.00'))
        self.assertEqual(transactions[0].occurred_at, statement_time)

    def test_initialize_opening_balances_writes_cash_and_positions(self) -> None:
        """Create an opening ledger directly from explicit asset balances."""
        statement_time = datetime(2020, 1, 2, 16, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_directory = create_portfolio(
                root,
                'playground',
                broker='historical',
                account_id='playground',
            )

            count = initialize_opening_balances(
                portfolio_directory,
                {'USD': Decimal(10000), 'AMAT': Decimal(22)},
                occurred_at=statement_time,
            )
            transactions = load_transactions(portfolio_directory / 'transactions.csv')

        self.assertEqual(count, 2)
        self.assertEqual(transactions[0].type, TransactionType.OPENING_CASH)
        self.assertEqual(transactions[0].amount, Decimal(10000))
        self.assertEqual(transactions[1].type, TransactionType.OPENING_POSITION)
        self.assertEqual(transactions[1].ticker, 'AMAT')
        self.assertEqual(transactions[1].quantity, Decimal(22))

    def test_initialize_opening_balances_validates_cash_precision(self) -> None:
        """Apply cash-flow precision rules to inline opening cash."""
        with tempfile.TemporaryDirectory() as directory:
            portfolio_directory = create_portfolio(
                Path(directory),
                'playground',
                broker='historical',
                account_id='playground',
            )

            with self.assertRaisesRegex(ValueError, 'fractions smaller than one cent'):
                initialize_opening_balances(
                    portfolio_directory,
                    {'USD': Decimal('100.005')},
                    occurred_at=datetime(2020, 1, 2, 16, tzinfo=UTC),
                )

            self.assertFalse((portfolio_directory / 'transactions.csv').exists())

    def test_create_portfolio_reuses_documentation_only_directory(self) -> None:
        """Preserve tracked scaffolding while creating portfolio metadata."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_directory = root / 'portfolio/playground'
            portfolio_directory.mkdir(parents=True)
            readme = portfolio_directory / 'README.md'
            readme.write_text('# Playground\n', encoding='utf-8')

            created = create_portfolio(
                root,
                'playground',
                broker='historical',
                account_id='playground',
            )

            self.assertEqual(created, portfolio_directory)
            self.assertEqual(readme.read_text(encoding='utf-8'), '# Playground\n')
            self.assertTrue((portfolio_directory / 'portfolio.yaml').is_file())

    def test_create_portfolio_refuses_existing_data(self) -> None:
        """Do not replace or add to a directory containing portfolio data."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_directory = root / 'portfolio/playground'
            portfolio_directory.mkdir(parents=True)
            ledger = portfolio_directory / 'transactions.csv'
            ledger.write_text('existing data\n', encoding='utf-8')

            with self.assertRaisesRegex(
                FileExistsError, 'already contains portfolio data: transactions.csv'
            ):
                create_portfolio(
                    root,
                    'playground',
                    broker='historical',
                    account_id='playground',
                )

            self.assertEqual(ledger.read_text(encoding='utf-8'), 'existing data\n')
            self.assertFalse((portfolio_directory / 'portfolio.yaml').exists())

    def test_import_opening_snapshot_does_not_replace_ledger(self) -> None:
        """Reject a second bootstrap import instead of replacing account facts."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'opening.csv'
            source.write_text(
                'asset,quantity\nAAPL,1\n',
                encoding='utf-8',
            )
            portfolio_directory = create_portfolio(
                root,
                'etrade-roth-ira',
                broker='etrade',
                account_id='roth-ira',
            )
            import_opening_snapshot(portfolio_directory, source)

            with self.assertRaises(FileExistsError):
                import_opening_snapshot(portfolio_directory, source)

    def test_import_activity_appends_dividend_and_reinvestment(self) -> None:
        """Append cash and trade events and preserve their source identities."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opening = root / 'opening.csv'
            opening.write_text('asset,amount\nUSD,1000.00\n', encoding='utf-8')
            activity = root / 'activity.csv'
            activity.write_text(
                'occurred_at,event,asset,quantity,amount,price,fees,external_id\n'
                '2020-03-13T12:00:00-04:00,dividend,USD,,24.60,,,div-1\n'
                '2020-03-13T12:01:00-04:00,buy,AAPL,0.09,,273.33,0,trade-1\n',
                encoding='utf-8',
            )
            portfolio_directory = create_portfolio(
                root,
                'brokerage',
                broker='historical',
                account_id='brokerage',
            )
            import_opening_snapshot(
                portfolio_directory,
                opening,
                occurred_at=datetime(2020, 1, 2, 16, tzinfo=UTC),
            )

            result = import_activity(portfolio_directory, activity)
            transactions = load_transactions(portfolio_directory / 'transactions.csv')

        self.assertEqual(result.imported, 2)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(transactions[1].type, TransactionType.DIVIDEND)
        self.assertEqual(transactions[1].amount, Decimal('24.60'))
        self.assertEqual(transactions[2].type, TransactionType.BUY)
        self.assertEqual(transactions[2].ticker, 'AAPL')
        self.assertEqual(transactions[2].external_id, 'trade-1')

    def test_import_activity_skips_identical_known_event(self) -> None:
        """Treat an overlapping broker export as an idempotent update."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opening = root / 'opening.csv'
            opening.write_text('asset,amount\nUSD,1000.00\n', encoding='utf-8')
            row = (
                'occurred_at,event,asset,amount,external_id\n'
                '2020-03-13T12:00:00-04:00,dividend,USD,24.60,div-1\n'
            )
            first = root / 'activity-march.csv'
            second = root / 'activity-ytd.csv'
            conflicting = root / 'activity-conflict.csv'
            first.write_text(row, encoding='utf-8')
            second.write_text(row, encoding='utf-8')
            conflicting.write_text(row.replace('24.60', '25.00'), encoding='utf-8')
            portfolio_directory = create_portfolio(
                root,
                'brokerage',
                broker='historical',
                account_id='brokerage',
            )
            import_opening_snapshot(
                portfolio_directory,
                opening,
                occurred_at=datetime(2020, 1, 2, 16, tzinfo=UTC),
            )
            import_activity(portfolio_directory, first)

            result = import_activity(portfolio_directory, second)
            repeated = import_activity(portfolio_directory, first)
            transactions = load_transactions(portfolio_directory / 'transactions.csv')
            with self.assertRaisesRegex(ValueError, 'conflicts with the existing'):
                import_activity(portfolio_directory, conflicting)
            self.assertFalse(
                (portfolio_directory / 'imports' / 'activity-conflict.csv').exists()
            )

        self.assertEqual(result.imported, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(repeated.imported, 0)
        self.assertEqual(repeated.skipped, 1)
        self.assertEqual(len(transactions), 2)

    def test_import_activity_explains_append_only_ordering(self) -> None:
        """Name user-facing facts when an import predates the current ledger."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opening = root / 'opening.csv'
            opening.write_text('asset,amount\nUSD,1000.00\n', encoding='utf-8')
            march = root / 'march.csv'
            march.write_text(
                'occurred_at,event,asset,amount,external_id\n'
                '2020-03-13T09:00:00-07:00,dividend,USD,24.60,DIV-MAR\n',
                encoding='utf-8',
            )
            february = root / 'february.csv'
            february.write_text(
                'occurred_at,event,asset,amount,external_id\n'
                '2020-02-13T09:00:00-08:00,dividend,USD,20.00,DIV-FEB\n',
                encoding='utf-8',
            )
            portfolio_directory = create_portfolio(
                root,
                'brokerage',
                broker='historical',
                account_id='brokerage',
            )
            import_opening_snapshot(
                portfolio_directory,
                opening,
                occurred_at=datetime(2020, 1, 2, 16, tzinfo=UTC),
            )
            import_activity(portfolio_directory, march)

            with self.assertRaisesRegex(
                ValueError,
                "february.csv.*'DIV-FEB'.*'DIV-MAR'.*append-only",
            ):
                import_activity(portfolio_directory, february)


if __name__ == '__main__':
    unittest.main()
