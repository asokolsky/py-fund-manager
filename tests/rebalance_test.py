"""Tests for derived portfolio state and rebalance order planning."""

import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from py_fund_manager.rebalance import (
    derive_portfolio_state,
    load_latest_daily_prices,
    plan_rebalance,
)
from py_fund_manager.schemas import (
    OrderReason,
    Portfolio,
    PriceObservation,
    Strategy,
    StrategyAssignment,
    StrategyRevisionReference,
    Transaction,
)
from py_fund_manager.strategy import strategy_revision

AS_OF = datetime(2026, 8, 26, 12, tzinfo=UTC)


def portfolio() -> Portfolio:
    """Return a fictional USD portfolio for rebalance tests."""
    return Portfolio(
        id='example-account',
        name='Example account',
        broker='example',
        account_id='example-account',
        base_currency='USD',
    )


def assignment(strategy: Strategy) -> StrategyAssignment:
    """Return the effective strategy assignment used by test plans."""
    return StrategyAssignment(
        id='assignment-test',
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        strategy=StrategyRevisionReference(
            id=strategy.id, revision=strategy_revision(strategy)
        ),
    )


def transaction(
    transaction_id: str, transaction_type: str, **values: object
) -> Transaction:
    """Build one confirmed fictional ledger transaction."""
    return Transaction.model_validate(
        {
            'id': transaction_id,
            'occurred_at': '2026-01-01T00:00:00Z',
            'type': transaction_type,
            'currency': 'USD',
            **values,
        }
    )


class TestRebalance(unittest.TestCase):
    """Verify state derivation, valuation prices, and strict order plans."""

    def test_derive_positions_and_cash_from_ledger(self) -> None:
        """Apply security and cash facts without treating plans as transactions."""
        transactions = [
            transaction('open-a', 'opening_position', ticker='AAPL', quantity='2'),
            transaction('cash', 'opening_cash', amount='100'),
            transaction(
                'buy',
                'buy',
                ticker='MSFT',
                quantity='1',
                price='50',
                fees='1',
            ),
            transaction(
                'sell',
                'sell',
                ticker='AAPL',
                quantity='0.5',
                amount='60',
                fees='2',
            ),
        ]

        positions, cash = derive_portfolio_state(portfolio(), transactions, AS_OF)

        self.assertEqual(positions, {'AAPL': Decimal('1.5'), 'MSFT': Decimal(1)})
        self.assertEqual(cash, Decimal(107))

    def test_plan_handles_missing_and_non_strategy_positions(self) -> None:
        """Buy missing targets and close holdings absent from a strict strategy."""
        transactions = [
            transaction('open-a', 'opening_position', ticker='AAPL', quantity='2'),
            transaction('open-b', 'opening_position', ticker='MSFT', quantity='1'),
            transaction('cash', 'opening_cash', amount='100'),
        ]
        strategy = Strategy.model_validate(
            {
                'id': 'target',
                'name': 'Target',
                'allocation': {
                    'type': 'target_weights',
                    'positions': {'AAPL': '0.6', 'NVDA': '0.4'},
                },
            }
        )
        prices = {
            ticker: PriceObservation(
                ticker=ticker,
                as_of=date(2026, 8, 26),
                price=price,
                currency='USD',
            )
            for ticker, price in {
                'AAPL': Decimal(100),
                'MSFT': Decimal(50),
                'NVDA': Decimal(25),
            }.items()
        }

        plan = plan_rebalance(
            portfolio(),
            transactions,
            assignment(strategy),
            strategy,
            prices,
            as_of=AS_OF,
            contribution=Decimal(50),
            generated_at=AS_OF,
        )
        orders = {order.ticker: order for order in plan.orders}

        self.assertEqual(orders['AAPL'].estimated_notional, Decimal('40.00'))
        self.assertEqual(orders['NVDA'].quantity, Decimal('6.400000'))
        self.assertEqual(orders['MSFT'].reason, OrderReason.NOT_IN_STRATEGY)
        self.assertEqual(orders['MSFT'].quantity, Decimal(1))
        self.assertEqual(plan.summary.estimated_buys, Decimal('200.00'))
        self.assertEqual(plan.summary.estimated_sells, Decimal('50.00'))
        self.assertEqual(plan.summary.estimated_ending_cash, Decimal('0.00'))
        serialized = json.loads(plan.model_dump_json())
        self.assertEqual(serialized['valuation']['contribution'], '50.00')
        self.assertEqual(serialized['orders'][0]['estimated_notional'], '40.00')

    def test_load_latest_daily_price_at_or_before_time(self) -> None:
        """Select the last eligible close and preserve its observation date."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'interval=1d/ticker=AAPL/year=2026/data.parquet'
            path.parent.mkdir(parents=True)
            table = pa.table(
                {
                    'date': [date(2026, 8, 25), date(2026, 8, 27)],
                    'close': [100.25, 101.50],
                }
            ).replace_schema_metadata({b'currency': b'USD'})
            pq.write_table(table, path)
            class_share_path = root / 'interval=1d/ticker=BRK-B/year=2026/data.parquet'
            class_share_path.parent.mkdir(parents=True)
            pq.write_table(table, class_share_path)

            prices = load_latest_daily_prices({'AAPL', 'BRK.B'}, AS_OF, 'USD', root)

        self.assertEqual(prices['AAPL'].as_of, date(2026, 8, 25))
        self.assertEqual(prices['AAPL'].price, Decimal('100.25'))
        self.assertEqual(prices['BRK.B'].price, Decimal('100.25'))

    def test_withdrawal_reduces_target_portfolio_value(self) -> None:
        """Reserve a planned withdrawal while generating the funding trades."""
        transactions = [
            transaction('open-a', 'opening_position', ticker='AAPL', quantity='2'),
            transaction('open-b', 'opening_position', ticker='MSFT', quantity='1'),
            transaction('cash', 'opening_cash', amount='100'),
        ]
        strategy = Strategy.model_validate(
            {
                'id': 'target',
                'name': 'Target',
                'allocation': {
                    'type': 'target_weights',
                    'positions': {'AAPL': '0.5', 'MSFT': '0.5'},
                },
            }
        )
        prices = {
            ticker: PriceObservation(
                ticker=ticker,
                as_of=date(2026, 8, 26),
                price=price,
                currency='USD',
            )
            for ticker, price in {
                'AAPL': Decimal(100),
                'MSFT': Decimal(50),
            }.items()
        }

        plan = plan_rebalance(
            portfolio(),
            transactions,
            assignment(strategy),
            strategy,
            prices,
            as_of=AS_OF,
            withdrawal=Decimal(50),
            generated_at=AS_OF,
        )

        self.assertEqual(plan.valuation.target_portfolio_value, Decimal('300.00'))
        self.assertEqual(plan.summary.estimated_sells, Decimal('50.00'))
        self.assertEqual(plan.summary.estimated_buys, Decimal('100.00'))
        self.assertEqual(plan.summary.estimated_ending_cash, Decimal('0.00'))


if __name__ == '__main__':
    unittest.main()
