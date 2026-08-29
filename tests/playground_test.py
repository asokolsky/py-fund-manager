"""Regression tests for the deterministic Playground portfolio lifecycle."""

import csv
import tempfile
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from py_fund_manager.broker import execute_rebalance_plan
from py_fund_manager.historical_broker import HistoricalBroker
from py_fund_manager.portfolio import (
    create_portfolio,
    import_activity,
    import_opening_snapshot,
    load_portfolio,
    load_strategy,
    load_transactions,
)
from py_fund_manager.rebalance import (
    derive_portfolio_state,
    load_latest_daily_prices,
    plan_rebalance,
)
from py_fund_manager.schemas import (
    StrategyAssignment,
    StrategyRevisionReference,
    Transaction,
)
from py_fund_manager.strategy import strategy_revision

OPENED_AT = datetime(2020, 1, 2, 16, tzinfo=UTC)
FIRST_PLAN_AT = datetime(2020, 1, 3, 15, tzinfo=UTC)
FIRST_EXECUTION_AT = datetime(2020, 1, 3, 21, tzinfo=UTC)
DIVIDEND_AT = datetime(2020, 3, 13, 16, tzinfo=UTC)
SECOND_PLAN_AT = datetime(2020, 3, 13, 21, tzinfo=UTC)
SECOND_EXECUTION_AT = SECOND_PLAN_AT
CONTRIBUTION_AT = datetime(2020, 6, 15, 16, tzinfo=UTC)
THIRD_PLAN_AT = datetime(2020, 6, 15, 21, tzinfo=UTC)
THIRD_EXECUTION_AT = THIRD_PLAN_AT
STRATEGY_PATH = Path(__file__).parents[1] / 'sample-data/strategy/mag7/strategy.yaml'


class TestPlayground(unittest.TestCase):
    """Cover the complete cash-funded Mag7 Playground workflow."""

    def test_open_rebalance_dividend_contribution_and_rebalance(self) -> None:
        """Replay three rebalances around a dividend and cash contribution."""
        strategy = load_strategy(STRATEGY_PATH)
        tickers = set(strategy.target_weights)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            price_history = root / 'stocks-by-ticker'
            self._write_price_history(price_history, tickers)
            portfolio_directory = create_portfolio(root, 'playground')
            opening = Path(__file__).parent / 'data/playground-opening.csv'
            import_opening_snapshot(
                portfolio_directory,
                opening,
                occurred_at=OPENED_AT,
            )
            portfolio = load_portfolio(portfolio_directory / 'portfolio.yaml')
            assignment = StrategyAssignment(
                id='initial-mag7',
                effective_at=OPENED_AT,
                strategy=StrategyRevisionReference(
                    name=strategy.metadata.name,
                    revision=strategy_revision(strategy),
                ),
            )

            first_prices = load_latest_daily_prices(
                tickers, FIRST_PLAN_AT, 'USD', price_history
            )
            opening_transactions = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            first_plan = plan_rebalance(
                portfolio,
                opening_transactions,
                assignment,
                strategy,
                first_prices,
                as_of=FIRST_PLAN_AT,
                generated_at=FIRST_PLAN_AT,
            )
            first_result = execute_rebalance_plan(
                HistoricalBroker(FIRST_EXECUTION_AT, price_history),
                portfolio,
                opening_transactions,
                first_plan,
            )
            executions_file = root / 'rebalance-2020-01-03.csv'
            self._write_executions(executions_file, first_result.transactions)
            execution_import = import_activity(portfolio_directory, executions_file)
            after_first = load_transactions(portfolio_directory / 'transactions.csv')
            _, cash_after_first = derive_portfolio_state(
                portfolio, after_first, DIVIDEND_AT
            )

            dividend_file = root / 'activity-2020-03-13.csv'
            dividend_file.write_text(
                'occurred_at,event,asset,amount,external_id\n'
                f'{DIVIDEND_AT.isoformat()},dividend,USD,70.00,playground-dividend-1\n',
                encoding='utf-8',
            )
            dividend_import = import_activity(portfolio_directory, dividend_file)
            before_second = load_transactions(portfolio_directory / 'transactions.csv')
            positions_before, cash_before = derive_portfolio_state(
                portfolio, before_second, SECOND_PLAN_AT
            )

            second_prices = load_latest_daily_prices(
                tickers, SECOND_PLAN_AT, 'USD', price_history
            )
            second_plan = plan_rebalance(
                portfolio,
                before_second,
                assignment,
                strategy,
                second_prices,
                as_of=SECOND_PLAN_AT,
                generated_at=SECOND_PLAN_AT,
            )
            second_result = execute_rebalance_plan(
                HistoricalBroker(SECOND_EXECUTION_AT, price_history),
                portfolio,
                before_second,
                second_plan,
            )
            second_executions_file = root / 'rebalance-2020-03-13.csv'
            self._write_executions(second_executions_file, second_result.transactions)
            second_execution_import = import_activity(
                portfolio_directory, second_executions_file
            )
            after_second = load_transactions(portfolio_directory / 'transactions.csv')
            positions_after_second, cash_after_second = derive_portfolio_state(
                portfolio, after_second, CONTRIBUTION_AT
            )

            contribution_file = root / 'activity-2020-06-15.csv'
            contribution_file.write_text(
                'occurred_at,event,asset,amount,external_id\n'
                f'{CONTRIBUTION_AT.isoformat()},deposit,USD,5000.00,'
                'playground-contribution-1\n',
                encoding='utf-8',
            )
            contribution_import = import_activity(
                portfolio_directory, contribution_file
            )
            before_third = load_transactions(portfolio_directory / 'transactions.csv')
            _, cash_before_third = derive_portfolio_state(
                portfolio, before_third, THIRD_PLAN_AT
            )

            third_prices = load_latest_daily_prices(
                tickers, THIRD_PLAN_AT, 'USD', price_history
            )
            third_plan = plan_rebalance(
                portfolio,
                before_third,
                assignment,
                strategy,
                third_prices,
                as_of=THIRD_PLAN_AT,
                generated_at=THIRD_PLAN_AT,
            )
            third_result = execute_rebalance_plan(
                HistoricalBroker(THIRD_EXECUTION_AT, price_history),
                portfolio,
                before_third,
                third_plan,
            )
            ledger_after_third_execution = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            third_executions_file = root / 'rebalance-2020-06-15.csv'
            self._write_executions(third_executions_file, third_result.transactions)
            third_execution_import = import_activity(
                portfolio_directory, third_executions_file
            )
            final_transactions = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            final_positions, final_cash = derive_portfolio_state(
                portfolio, final_transactions, THIRD_EXECUTION_AT
            )

            preserved_imports = {
                path.name for path in (portfolio_directory / 'imports').iterdir()
            }

        self.assertEqual(execution_import.imported, 7)
        self.assertEqual(dividend_import.imported, 1)
        first_order = first_result.orders[0]
        self.assertEqual(first_order.id, 'playground-20200103T150000000000Z-0001')
        self.assertEqual(first_order.ticker, 'AAPL')
        self.assertGreater(first_order.quantity, Decimal(0))
        self.assertEqual(first_order.submitted_at, FIRST_PLAN_AT)
        self.assertEqual(len(first_result.executions), 7)
        self.assertTrue(
            all(
                execution.price != first_prices[execution.ticker].price
                for execution in first_result.executions
            )
        )
        self.assertEqual(set(positions_before), set(strategy.target_weights))
        self.assertEqual(cash_before, cash_after_first + 70)
        self.assertEqual(len(second_result.executions), 7)
        self.assertEqual(second_execution_import.imported, 7)
        self.assertEqual(set(positions_after_second), set(strategy.target_weights))
        self.assertGreaterEqual(cash_after_second, Decimal(0))
        self.assertLess(cash_after_second, Decimal('0.01'))
        self.assertEqual(contribution_import.imported, 1)
        self.assertEqual(cash_before_third, cash_after_second + Decimal(5000))
        self.assertEqual(len(third_result.executions), 7)
        self.assertEqual(ledger_after_third_execution, before_third)
        self.assertEqual(third_execution_import.imported, 7)
        self.assertEqual(
            preserved_imports,
            {
                'playground-opening.csv',
                'rebalance-2020-01-03.csv',
                'activity-2020-03-13.csv',
                'rebalance-2020-03-13.csv',
                'activity-2020-06-15.csv',
                'rebalance-2020-06-15.csv',
            },
        )
        self.assertEqual(set(final_positions), set(strategy.target_weights))
        self.assertGreaterEqual(final_cash, Decimal(0))
        self.assertLess(final_cash, Decimal('0.01'))

    @staticmethod
    def _write_price_history(directory: Path, tickers: set[str]) -> None:
        """Write deterministic daily-price fixtures for every strategy ticker."""
        dates = [
            date(2020, 1, 2),
            date(2020, 1, 3),
            date(2020, 3, 13),
            date(2020, 6, 15),
        ]
        closes = [100.0, 99.0, 110.0, 120.0]
        for ticker in tickers:
            path = (
                directory
                / 'interval=1d'
                / f'ticker={ticker}'
                / 'year=2020/data.parquet'
            )
            path.parent.mkdir(parents=True)
            table = pa.table({'date': dates, 'close': closes}).replace_schema_metadata(
                {
                    b'currency': b'USD',
                    b'source': b'Playground test fixture',
                    b'exchange_timezone': b'America/New_York',
                }
            )
            pq.write_table(table, path)

    @staticmethod
    def _write_executions(path: Path, transactions: tuple[Transaction, ...]) -> None:
        """Write confirmed simulated purchases as canonical activity input."""
        fields = (
            'occurred_at',
            'event',
            'asset',
            'quantity',
            'amount',
            'price',
            'fees',
            'external_id',
        )
        with path.open('w', newline='', encoding='utf-8') as activity_file:
            writer = csv.DictWriter(activity_file, fieldnames=fields)
            writer.writeheader()
            for transaction in transactions:
                writer.writerow(
                    {
                        'occurred_at': transaction.occurred_at.isoformat(),
                        'event': transaction.type,
                        'asset': transaction.ticker,
                        'quantity': transaction.quantity,
                        'amount': transaction.amount,
                        'price': transaction.price,
                        'fees': transaction.fees,
                        'external_id': transaction.external_id,
                    }
                )


if __name__ == '__main__':
    unittest.main()
