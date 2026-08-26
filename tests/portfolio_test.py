"""Tests for portfolio, transaction, and strategy storage models."""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from py_fund_manager.portfolio import (
    create_portfolio,
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
                """schema_version: 1
id: etrade-roth-ira
name: E*TRADE Roth IRA
broker: etrade
account_id: "...1234"
base_currency: usd
opened_on: 2020-04-15
""",
                encoding='utf-8',
            )
            portfolio = load_portfolio(path)

        self.assertEqual(portfolio.id, 'etrade-roth-ira')
        self.assertEqual(portfolio.base_currency, 'USD')
        self.assertEqual(portfolio.opened_on, date(2020, 4, 15))

    def test_load_strategy_requires_weights_to_total_one(self) -> None:
        """Accept precise weights and reject an incomplete allocation."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'strategy.yaml'
            path.write_text(
                """schema_version: 1
id: balanced
name: Balanced
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

    def test_generated_sp500_strategy_is_complete(self) -> None:
        """Validate the committed direct-replication allocation."""
        strategy_path = (
            Path(__file__).parents[1]
            / 'sample-data/strategies/SnP500-direct/strategy.yaml'
        )
        strategy = load_strategy(strategy_path)

        self.assertEqual(strategy.id, 'SnP500-direct')
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
                    'id': 'etrade-roth-ira',
                    'name': 'Roth IRA',
                    'broker': 'etrade',
                    'account_id': 'local-account',
                    'base_currency': 'USD',
                    'curreny': 'USD',
                }
            )

    def test_portfolio_rejects_strategy_pointer(self) -> None:
        """Keep effective strategy selection solely in strategy history."""
        with self.assertRaises(ValidationError):
            Portfolio.model_validate(
                {
                    'id': 'etrade-roth-ira',
                    'name': 'Roth IRA',
                    'broker': 'etrade',
                    'account_id': 'local-account',
                    'base_currency': 'USD',
                    'strategy': 'SnP500-direct',
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

        self.assertEqual(portfolio.id, 'etrade-brokerage')
        self.assertEqual(portfolio.broker, 'etrade')
        self.assertEqual(portfolio.account_id, 'etrade-brokerage')

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
