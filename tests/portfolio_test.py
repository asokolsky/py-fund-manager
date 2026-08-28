"""Tests for portfolio, transaction, and strategy storage models."""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from py_fund_manager.portfolio import (
    create_portfolio,
    find_manifest,
    import_opening_positions,
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
            portfolio_directory = create_portfolio(Path(directory), 'sample')
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
            portfolio_directory = create_portfolio(Path(directory), 'sample')
            duplicate = portfolio_directory / 'duplicate.yaml'
            duplicate.write_text(
                (portfolio_directory / 'portfolio.yaml').read_text(), encoding='utf-8'
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
            portfolio_directory = create_portfolio(Path(directory), 'etrade-brokerage')
            portfolio = load_portfolio(portfolio_directory / 'portfolio.yaml')

        self.assertEqual(portfolio.metadata.name, 'etrade-brokerage')
        self.assertEqual(portfolio.spec.broker, 'etrade')
        self.assertEqual(portfolio.spec.account_id, 'etrade-brokerage')

    def test_import_opening_positions_preserves_source_and_writes_ledger(
        self,
    ) -> None:
        """Convert canonical holdings into a loadable opening ledger."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'stocks.csv'
            source.write_text(
                'ticker,quantity,cost_basis,currency\nAAPL,12.5,2100.00,USD\n',
                encoding='utf-8',
            )
            portfolio_directory = create_portfolio(root, 'etrade-roth-ira')

            count = import_opening_positions(portfolio_directory, source)
            transactions = load_transactions(portfolio_directory / 'transactions.csv')

            self.assertEqual(count, 1)
            self.assertEqual(transactions[0].quantity, Decimal('12.5'))
            self.assertEqual(transactions[0].cost_basis, Decimal('2100.00'))
            self.assertEqual(
                (portfolio_directory / 'imports' / 'stocks.csv').read_text(),
                source.read_text(),
            )

    def test_import_opening_positions_does_not_replace_ledger(self) -> None:
        """Reject a second bootstrap import instead of replacing account facts."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'stocks.csv'
            source.write_text('ticker,quantity\nAAPL,1\n', encoding='utf-8')
            portfolio_directory = create_portfolio(root, 'etrade-roth-ira')
            import_opening_positions(portfolio_directory, source)

            with self.assertRaises(FileExistsError):
                import_opening_positions(portfolio_directory, source)


if __name__ == '__main__':
    unittest.main()
