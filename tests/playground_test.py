"""Regression tests for the deterministic Playground portfolio lifecycle."""

import csv
import tempfile
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

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
from py_fund_manager.rebalance import derive_portfolio_state, plan_rebalance
from py_fund_manager.schemas import (
    PriceObservation,
    StrategyAssignment,
    StrategyRevisionReference,
    Transaction,
)
from py_fund_manager.strategy import strategy_revision

OPENED_AT = datetime(2020, 1, 2, 16, tzinfo=UTC)
FIRST_REBALANCE_AT = datetime(2020, 1, 3, 21, tzinfo=UTC)
DIVIDEND_AT = datetime(2020, 3, 13, 16, tzinfo=UTC)
SECOND_REBALANCE_AT = datetime(2020, 3, 13, 21, tzinfo=UTC)


class TestPlayground(unittest.TestCase):
    """Protect the complete cash-funded Mag7 Playground workflow."""

    def test_open_rebalance_credit_dividend_and_rebalance_again(self) -> None:
        """Replay two deterministic rebalances around an imported dividend."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_directory = create_portfolio(root, 'playground')
            opening = root / 'opening.csv'
            opening.write_text(
                'asset,quantity,amount,cost_basis\nUSD,,100000.00,\n',
                encoding='utf-8',
            )
            import_opening_snapshot(
                portfolio_directory,
                opening,
                occurred_at=OPENED_AT,
            )
            portfolio = load_portfolio(portfolio_directory / 'portfolio.yaml')
            strategy = load_strategy(
                Path(__file__).parents[1] / 'sample-data/strategy/mag7/strategy.yaml'
            )
            assignment = StrategyAssignment(
                id='initial-mag7',
                effective_at=OPENED_AT,
                strategy=StrategyRevisionReference(
                    name=strategy.metadata.name,
                    revision=strategy_revision(strategy),
                ),
            )

            first_prices = self._prices(strategy.target_weights, FIRST_REBALANCE_AT)
            opening_transactions = load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            first_plan = plan_rebalance(
                portfolio,
                opening_transactions,
                assignment,
                strategy,
                first_prices,
                as_of=FIRST_REBALANCE_AT,
                generated_at=FIRST_REBALANCE_AT,
            )
            first_result = execute_rebalance_plan(
                HistoricalBroker(first_prices),
                portfolio,
                opening_transactions,
                first_plan,
            )
            executions_file = root / 'activity-first-rebalance.csv'
            self._write_executions(executions_file, first_result.transactions)
            execution_import = import_activity(portfolio_directory, executions_file)

            dividend_file = root / 'activity-dividend.csv'
            dividend_file.write_text(
                'occurred_at,event,asset,amount,external_id\n'
                f'{DIVIDEND_AT.isoformat()},dividend,USD,70.00,playground-dividend-1\n',
                encoding='utf-8',
            )
            dividend_import = import_activity(portfolio_directory, dividend_file)
            before_second = load_transactions(portfolio_directory / 'transactions.csv')
            positions_before, cash_before = derive_portfolio_state(
                portfolio, before_second, SECOND_REBALANCE_AT
            )

            second_prices = self._prices(strategy.target_weights, SECOND_REBALANCE_AT)
            second_plan = plan_rebalance(
                portfolio,
                before_second,
                assignment,
                strategy,
                second_prices,
                as_of=SECOND_REBALANCE_AT,
                generated_at=SECOND_REBALANCE_AT,
            )
            second_result = execute_rebalance_plan(
                HistoricalBroker(second_prices),
                portfolio,
                before_second,
                second_plan,
            )
            positions_after, cash_after = derive_portfolio_state(
                portfolio,
                [*before_second, *second_result.transactions],
                SECOND_REBALANCE_AT,
            )

        self.assertEqual(execution_import.imported, 7)
        self.assertEqual(dividend_import.imported, 1)
        self.assertEqual(len(first_result.executions), 7)
        self.assertEqual(set(positions_before), set(strategy.target_weights))
        self.assertEqual(cash_before, first_plan.summary.estimated_ending_cash + 70)
        self.assertEqual(len(second_result.executions), 7)
        self.assertEqual(set(positions_after), set(strategy.target_weights))
        self.assertEqual(cash_after, second_plan.summary.estimated_ending_cash)
        self.assertGreaterEqual(cash_after, Decimal(0))
        self.assertLess(cash_after, Decimal('0.01'))

    @staticmethod
    def _prices(
        tickers: dict[str, Decimal], available_at: datetime
    ) -> dict[str, PriceObservation]:
        """Build deterministic USD 100 observations for every strategy ticker."""
        return {
            ticker: PriceObservation(
                ticker=ticker,
                as_of=date.fromisoformat(available_at.date().isoformat()),
                available_at=available_at,
                price=Decimal(100),
                currency='USD',
                source='Playground regression fixture',
                source_partition=(
                    f'interval=1d/ticker={ticker}/year={available_at.year}/data.parquet'
                ),
            )
            for ticker in tickers
        }

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
