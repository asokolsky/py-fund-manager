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
    DisplayMetadata,
    OrderReason,
    Portfolio,
    PortfolioSpec,
    PriceObservation,
    Strategy,
    StrategyAssignment,
    StrategyRevisionReference,
    StrategySpec,
    TargetAllocation,
    Transaction,
)
from py_fund_manager.strategy import strategy_revision

AS_OF = datetime(2026, 8, 26, 12, tzinfo=UTC)


def portfolio() -> Portfolio:
    """Return a fictional USD portfolio for rebalance tests."""
    return Portfolio(
        apiVersion='v1',
        kind='Portfolio',
        metadata=DisplayMetadata(
            name='example-account', display_name='Example account'
        ),
        spec=PortfolioSpec(
            broker='example', account_id='example-account', base_currency='USD'
        ),
    )


def assignment(strategy: Strategy) -> StrategyAssignment:
    """Return the effective strategy assignment used by test plans."""
    return StrategyAssignment(
        id='assignment-test',
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        strategy=StrategyRevisionReference(
            name=strategy.metadata.name, revision=strategy_revision(strategy)
        ),
    )


def target_strategy(positions: dict[str, str]) -> Strategy:
    """Return a fictional target-weight Strategy manifest."""
    return Strategy(
        apiVersion='v1',
        kind='Strategy',
        metadata=DisplayMetadata(name='target', display_name='Target'),
        spec=StrategySpec(
            allocation=TargetAllocation.model_validate(
                {'type': 'target_weights', 'positions': positions}
            )
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


def price_observation(ticker: str, price: Decimal) -> PriceObservation:
    """Return a coherent fictional price available before the planning time."""
    return PriceObservation(
        ticker=ticker,
        as_of=date(2026, 8, 26),
        available_at=datetime(2026, 8, 26, 10, tzinfo=UTC),
        price=price,
        currency='USD',
        source='Test prices',
        source_partition=f'interval=1d/ticker={ticker}/year=2026/data.parquet',
    )


def write_daily_prices(
    path: Path,
    dates: list[date],
    closes: list[float],
    *,
    currency: str = 'USD',
    source: str = 'Test prices',
    exchange_timezone: str = 'America/New_York',
) -> None:
    """Write one daily-price partition with required provenance metadata."""
    path.parent.mkdir(parents=True)
    table = pa.table({'date': dates, 'close': closes}).replace_schema_metadata(
        {
            b'currency': currency.encode(),
            b'source': source.encode(),
            b'exchange_timezone': exchange_timezone.encode(),
        }
    )
    pq.write_table(table, path)


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
            transaction('deposit', 'deposit', amount='50'),
        ]
        strategy = target_strategy({'AAPL': '0.6', 'NVDA': '0.4'})
        prices = {
            ticker: price_observation(ticker, price)
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
        self.assertNotIn('contribution', serialized['valuation'])
        self.assertEqual(serialized['orders'][0]['estimated_notional'], '40.000000')

    def test_executable_amounts_preserve_residual_cash(self) -> None:
        """Derive exact notionals and ending cash from rounded quantities."""
        transactions = [transaction('cash', 'opening_cash', amount='100')]
        strategy = target_strategy({'AAPL': '1'})
        plan = plan_rebalance(
            portfolio(),
            transactions,
            assignment(strategy),
            strategy,
            {'AAPL': price_observation('AAPL', Decimal(3))},
            as_of=AS_OF,
            generated_at=AS_OF,
        )

        order = plan.orders[0]
        self.assertEqual(plan.schema_version, 3)
        self.assertEqual(order.quantity, Decimal('33.333333'))
        self.assertEqual(
            order.estimated_notional, order.quantity * order.estimated_price
        )
        self.assertEqual(plan.summary.estimated_buys, Decimal('99.999999'))
        self.assertEqual(plan.summary.estimated_ending_cash, Decimal('0.000001'))

    def test_plan_rejects_subcent_withdrawal(self) -> None:
        """Enforce withdrawal precision for direct library callers."""
        strategy = target_strategy({'AAPL': '1'})
        with self.assertRaisesRegex(ValueError, 'fractions smaller than one cent'):
            plan_rebalance(
                portfolio(),
                [transaction('cash', 'opening_cash', amount='100')],
                assignment(strategy),
                strategy,
                {'AAPL': price_observation('AAPL', Decimal(100))},
                as_of=AS_OF,
                withdrawal=Decimal('100.005'),
                generated_at=AS_OF,
            )

    def test_plan_preserves_subcent_available_cash(self) -> None:
        """Keep exact ledger cash so a later plan reconciles to its summary."""
        transactions = [transaction('cash', 'opening_cash', amount='0.000001')]
        strategy = target_strategy({'AAPL': '1'})

        plan = plan_rebalance(
            portfolio(),
            transactions,
            assignment(strategy),
            strategy,
            {'AAPL': price_observation('AAPL', Decimal(3))},
            as_of=AS_OF,
            generated_at=AS_OF,
        )

        self.assertEqual(plan.valuation.available_cash, Decimal('0.000001'))
        self.assertEqual(plan.summary.estimated_ending_cash, Decimal('0.000001'))

    def test_load_latest_daily_price_at_or_before_time(self) -> None:
        """Select the last eligible close and preserve its observation date."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'interval=1d/ticker=AAPL/year=2026/data.parquet'
            write_daily_prices(
                path,
                [date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)],
                [100.25, 101.00, 101.50],
            )
            class_share_path = root / 'interval=1d/ticker=BRK-B/year=2026/data.parquet'
            write_daily_prices(
                class_share_path,
                [date(2026, 8, 25)],
                [100.25],
            )

            prices = load_latest_daily_prices({'AAPL', 'BRK.B'}, AS_OF, 'USD', root)

        self.assertEqual(prices['AAPL'].as_of, date(2026, 8, 25))
        self.assertEqual(prices['AAPL'].price, Decimal('100.25'))
        self.assertEqual(prices['BRK.B'].price, Decimal('100.25'))
        self.assertEqual(
            prices['AAPL'].source_partition,
            'interval=1d/ticker=AAPL/year=2026/data.parquet',
        )

    def test_same_day_close_requires_exchange_close_time(self) -> None:
        """Exclude a same-day close until 16:00 in the partition timezone."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'interval=1d/ticker=AAPL/year=2026/data.parquet'
            write_daily_prices(
                path,
                [date(2026, 8, 25), date(2026, 8, 26)],
                [100.25, 101.00],
            )

            before_close = load_latest_daily_prices(
                {'AAPL'}, datetime(2026, 8, 26, 19, 59, tzinfo=UTC), 'USD', root
            )
            at_close = load_latest_daily_prices(
                {'AAPL'}, datetime(2026, 8, 26, 20, 0, tzinfo=UTC), 'USD', root
            )

        self.assertEqual(before_close['AAPL'].as_of, date(2026, 8, 25))
        self.assertEqual(at_close['AAPL'].as_of, date(2026, 8, 26))
        self.assertEqual(
            at_close['AAPL'].available_at,
            datetime.fromisoformat('2026-08-26T16:00:00-04:00'),
        )

    def test_latest_price_keeps_its_partition_metadata(self) -> None:
        """Keep price metadata together regardless of partition creation order."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            newest = root / 'interval=1d/ticker=AAPL/year=2026/data.parquet'
            oldest = root / 'interval=1d/ticker=AAPL/year=2025/data.parquet'
            write_daily_prices(
                newest,
                [date(2026, 8, 25)],
                [101.00],
                source='New source',
            )
            write_daily_prices(
                oldest,
                [date(2025, 12, 31)],
                [90.00],
                currency='EUR',
                source='Old source',
            )

            prices = load_latest_daily_prices({'AAPL'}, AS_OF, 'USD', root)

        self.assertEqual(prices['AAPL'].price, Decimal('101.0'))
        self.assertEqual(prices['AAPL'].currency, 'USD')
        self.assertEqual(prices['AAPL'].source, 'New source')
        self.assertIn('year=2026', prices['AAPL'].source_partition)

    def test_withdrawal_reduces_target_portfolio_value(self) -> None:
        """Reserve a planned withdrawal while generating the funding trades."""
        transactions = [
            transaction('open-a', 'opening_position', ticker='AAPL', quantity='2'),
            transaction('open-b', 'opening_position', ticker='MSFT', quantity='1'),
            transaction('cash', 'opening_cash', amount='100'),
        ]
        strategy = target_strategy({'AAPL': '0.5', 'MSFT': '0.5'})
        prices = {
            ticker: price_observation(ticker, price)
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

    def test_sell_rounding_fully_funds_withdrawal(self) -> None:
        """Round a sale up by one increment rather than underfund a withdrawal."""
        transactions = [
            transaction('open-a', 'opening_position', ticker='AAPL', quantity='1')
        ]
        strategy = target_strategy({'AAPL': '1'})
        plan = plan_rebalance(
            portfolio(),
            transactions,
            assignment(strategy),
            strategy,
            {'AAPL': price_observation('AAPL', Decimal(3))},
            as_of=AS_OF,
            withdrawal=Decimal('0.01'),
            generated_at=AS_OF,
        )

        order = plan.orders[0]
        self.assertEqual(order.quantity, Decimal('0.003334'))
        self.assertLessEqual(order.quantity, order.current_quantity)
        self.assertEqual(order.estimated_notional, Decimal('0.010002'))
        self.assertEqual(plan.summary.estimated_ending_cash, Decimal('0.000002'))

    def test_plan_is_independent_of_transaction_order(self) -> None:
        """Produce identical orders when commutative ledger rows are reordered."""
        transactions = [
            transaction('open-a', 'opening_position', ticker='AAPL', quantity='2'),
            transaction('cash', 'opening_cash', amount='100'),
            transaction('deposit', 'deposit', amount='25'),
        ]
        strategy = target_strategy({'AAPL': '0.5', 'MSFT': '0.5'})
        prices = {
            'AAPL': price_observation('AAPL', Decimal(100)),
            'MSFT': price_observation('MSFT', Decimal(50)),
        }

        forward = plan_rebalance(
            portfolio(),
            transactions,
            assignment(strategy),
            strategy,
            prices,
            as_of=AS_OF,
            generated_at=AS_OF,
        )
        reverse = plan_rebalance(
            portfolio(),
            list(reversed(transactions)),
            assignment(strategy),
            strategy,
            prices,
            as_of=AS_OF,
            generated_at=AS_OF,
        )

        self.assertEqual(forward, reverse)


if __name__ == '__main__':
    unittest.main()
