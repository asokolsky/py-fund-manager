"""Regression tests for the deterministic Playground portfolio lifecycle."""

import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from py_fund_manager.broker import HistoricalBroker, execute_rebalance_plan
from py_fund_manager.portfolio import (
    create_portfolio,
    import_activity,
    initialize_opening_balances,
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
    Execution,
    OrderSide,
    StrategyAssignment,
    StrategyRevisionReference,
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
WITHDRAWAL_PLAN_AT = datetime(2020, 9, 1, 21, tzinfo=UTC)
WITHDRAWAL_EXECUTION_AT = datetime(2020, 9, 2, 21, tzinfo=UTC)
WITHDRAWAL_AT = datetime(2020, 9, 3, 16, tzinfo=UTC)
STRATEGY_PATH = Path(__file__).parents[1] / 'sample-data/strategy/mag7/strategy.yaml'


class TestPlayground(unittest.TestCase):
    """Cover the complete cash-funded Mag7 Playground workflow."""

    def test_complete_historical_cash_flow_workflow(self) -> None:
        """Replay rebalances around a dividend, contribution, and withdrawal."""
        strategy = load_strategy(STRATEGY_PATH)
        tickers = set(strategy.target_weights)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            price_history = root / 'stocks-by-ticker'
            self._write_price_history(price_history, tickers)
            portfolio_directory = create_portfolio(
                root,
                'playground',
                broker='historical',
                account_id='playground',
            )
            initialize_opening_balances(
                portfolio_directory,
                {'USD': Decimal('100000.00')},
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
            executions_file = root / 'executions-2020-01-03.json'
            self._write_executions(executions_file, first_result.executions)
            execution_import = import_activity(portfolio_directory, executions_file)
            after_first = load_transactions(portfolio_directory / 'transactions.csv')
            _, cash_after_first = derive_portfolio_state(
                portfolio, after_first, DIVIDEND_AT
            )

            dividend_file = Path(__file__).parent / 'data/activity-2020-03-13.csv'
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
            second_executions_file = root / 'executions-2020-03-13.json'
            self._write_executions(second_executions_file, second_result.executions)
            second_execution_import = import_activity(
                portfolio_directory, second_executions_file
            )
            after_second = load_transactions(portfolio_directory / 'transactions.csv')
            positions_after_second, cash_after_second = derive_portfolio_state(
                portfolio, after_second, CONTRIBUTION_AT
            )

            contribution_file = Path(__file__).parent / 'data/activity-2020-06-15.csv'
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
            third_executions_file = root / 'executions-2020-06-15.json'
            self._write_executions(third_executions_file, third_result.executions)
            third_execution_import = import_activity(
                portfolio_directory, third_executions_file
            )
            final_transactions = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            final_positions, final_cash = derive_portfolio_state(
                portfolio, final_transactions, THIRD_EXECUTION_AT
            )

            withdrawal_prices = load_latest_daily_prices(
                tickers, WITHDRAWAL_PLAN_AT, 'USD', price_history
            )
            withdrawal_plan = plan_rebalance(
                portfolio,
                final_transactions,
                assignment,
                strategy,
                withdrawal_prices,
                as_of=WITHDRAWAL_PLAN_AT,
                withdrawal=Decimal(1000),
                generated_at=WITHDRAWAL_PLAN_AT,
            )
            withdrawal_result = execute_rebalance_plan(
                HistoricalBroker(WITHDRAWAL_EXECUTION_AT, price_history),
                portfolio,
                final_transactions,
                withdrawal_plan,
            )
            ledger_after_withdrawal_execution = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            withdrawal_executions_file = root / 'executions-2020-09-02.json'
            self._write_executions(
                withdrawal_executions_file, withdrawal_result.executions
            )
            withdrawal_execution_import = import_activity(
                portfolio_directory, withdrawal_executions_file
            )
            before_withdrawal = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            positions_before_withdrawal, cash_before_withdrawal = (
                derive_portfolio_state(portfolio, before_withdrawal, WITHDRAWAL_AT)
            )

            withdrawal_file = Path(__file__).parent / 'data/activity-2020-09-03.csv'
            withdrawal_import = import_activity(portfolio_directory, withdrawal_file)
            after_withdrawal = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            positions_after_withdrawal, cash_after_withdrawal = derive_portfolio_state(
                portfolio, after_withdrawal, WITHDRAWAL_AT
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
        self.assertLess(
            cash_after_second,
            second_plan.summary.estimated_ending_cash
            + sum(
                (
                    execution.price * Decimal('0.001')
                    for execution in second_result.executions
                    if execution.side == OrderSide.BUY
                ),
                Decimal(0),
            ),
        )
        self.assertEqual(contribution_import.imported, 1)
        self.assertEqual(cash_before_third, cash_after_second + Decimal(5000))
        self.assertEqual(len(third_result.executions), 7)
        self.assertEqual(ledger_after_third_execution, before_third)
        self.assertEqual(third_execution_import.imported, 7)
        self.assertEqual(
            preserved_imports,
            {
                'executions-2020-01-03.json',
                'activity-2020-03-13.csv',
                'executions-2020-03-13.json',
                'activity-2020-06-15.csv',
                'executions-2020-06-15.json',
                'executions-2020-09-02.json',
                'activity-2020-09-03.csv',
            },
        )
        self.assertEqual(set(final_positions), set(strategy.target_weights))
        self.assertGreaterEqual(final_cash, Decimal(0))
        self.assertLess(
            final_cash,
            third_plan.summary.estimated_ending_cash
            + sum(
                (
                    execution.price * Decimal('0.001')
                    for execution in third_result.executions
                    if execution.side == OrderSide.BUY
                ),
                Decimal(0),
            ),
        )
        self.assertEqual(withdrawal_plan.valuation.withdrawal, Decimal('1000.00'))
        self.assertTrue(withdrawal_plan.orders)
        self.assertTrue(
            all(order.side == OrderSide.SELL for order in withdrawal_plan.orders)
        )
        self.assertEqual(
            ledger_after_withdrawal_execution,
            final_transactions,
        )
        self.assertEqual(
            withdrawal_execution_import.imported,
            len(withdrawal_result.executions),
        )
        self.assertGreaterEqual(cash_before_withdrawal, Decimal(1000))
        self.assertEqual(withdrawal_import.imported, 1)
        self.assertEqual(positions_after_withdrawal, positions_before_withdrawal)
        self.assertEqual(cash_after_withdrawal, cash_before_withdrawal - Decimal(1000))

    @staticmethod
    def _write_price_history(directory: Path, tickers: set[str]) -> None:
        """Write deterministic daily-price fixtures for every strategy ticker."""
        dates = [
            date(2020, 1, 2),
            date(2020, 1, 3),
            date(2020, 3, 13),
            date(2020, 6, 15),
            date(2020, 9, 1),
            date(2020, 9, 2),
        ]
        closes = [100.0, 99.0, 110.0, 120.0, 130.0, 131.0]
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
    def _write_executions(path: Path, executions: tuple[Execution, ...]) -> None:
        """Write canonical broker executions exactly as the CLI emits them."""
        path.write_text(
            json.dumps(
                [execution.model_dump(mode='json') for execution in executions],
                indent=2,
            )
            + '\n',
            encoding='utf-8',
        )


if __name__ == '__main__':
    unittest.main()
