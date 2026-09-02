"""Tests for loading and displaying portfolio snapshots."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from textual.widgets import DataTable, OptionList, Static

from py_fund_manager.browser import (
    AsOfScreen,
    PortfolioBrowserApp,
    PortfolioSnapshot,
    PositionSnapshot,
    latest_portfolio_timestamp,
    load_portfolio_snapshots,
    load_portfolio_timestamps,
)
from py_fund_manager.schemas import PriceObservation


class TestPortfolioSnapshots(unittest.TestCase):
    """Verify deterministic discovery and effective-dated state derivation."""

    def test_latest_portfolio_timestamp_excludes_future_facts(self) -> None:
        """Choose the latest past transaction even when a future one is closer."""
        before = datetime(2026, 8, 31, 12, tzinfo=UTC)
        after = datetime(2026, 9, 1, 13, tzinfo=UTC)

        selected = latest_portfolio_timestamp(
            (after, before), datetime(2026, 9, 1, 12, tzinfo=UTC)
        )

        self.assertEqual(selected, before)

    def test_latest_portfolio_timestamp_requires_an_eligible_time(self) -> None:
        """Reject a default valuation when every transaction is future-dated."""
        future = datetime(2026, 9, 2, tzinfo=UTC)
        with self.assertRaisesRegex(
            ValueError,
            'no portfolio transaction timestamps are available at or before now',
        ):
            latest_portfolio_timestamp((future,), datetime(2026, 9, 1, tzinfo=UTC))

    def test_load_all_portfolios_at_requested_time(self) -> None:
        """Discover manifests, ignore scaffolding, and exclude future facts."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_root = root / 'portfolio'
            (portfolio_root / 'notes').mkdir(parents=True)
            (portfolio_root / 'notes/README.md').write_text('Scaffolding only')
            account = portfolio_root / 'brokerage'
            account.mkdir()
            (account / 'account.yaml').write_text(
                'apiVersion: v1\n'
                'kind: Portfolio\n'
                'metadata:\n'
                '  name: brokerage\n'
                '  display_name: Brokerage\n'
                'spec:\n'
                '  broker: historical\n'
                '  account_id: acct-123\n'
                '  base_currency: USD\n',
                encoding='utf-8',
            )
            (account / 'transactions.csv').write_text(
                'id,occurred_at,type,ticker,quantity,price,amount,cost_basis,'
                'currency,fees,external_id\n'
                'opening,2026-01-01T00:00:00+00:00,opening_cash,,,,100,,USD,0,\n'
                'shares,2026-01-01T00:00:00+00:00,opening_position,AAPL,2,,,10,USD,0,\n'
                'future,2027-01-01T00:00:00+00:00,deposit,,,,50,,USD,0,\n',
                encoding='utf-8',
            )

            as_of = datetime(2026, 6, 1, tzinfo=UTC)
            price = PriceObservation(
                ticker='AAPL',
                as_of=date(2026, 5, 29),
                available_at=datetime(2026, 5, 29, 20, tzinfo=UTC),
                price=Decimal(25),
                currency='USD',
                source='test',
                source_partition='test.parquet',
            )
            with patch(
                'py_fund_manager.browser.load_latest_daily_prices',
                return_value={'AAPL': price},
            ):
                snapshots = load_portfolio_snapshots(root, None, as_of)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].portfolio_id, 'brokerage')
        self.assertEqual(snapshots[0].cash, Decimal(100))
        self.assertEqual(snapshots[0].positions_value, Decimal(50))
        self.assertEqual(snapshots[0].total_value, Decimal(150))
        self.assertEqual(snapshots[0].transaction_count, 2)
        self.assertEqual(
            snapshots[0].warnings,
            ('Prices are older than 2026-06-01 for: AAPL',),
        )

    def test_named_portfolio_must_exist(self) -> None:
        """Report a clear error instead of opening an empty TUI portfolio browser."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'portfolio').mkdir()
            with self.assertRaisesRegex(
                ValueError, "portfolio 'missing' does not exist"
            ):
                load_portfolio_snapshots(
                    root, 'missing', datetime(2026, 1, 1, tzinfo=UTC)
                )

    def test_named_portfolio_does_not_value_unrelated_portfolios(self) -> None:
        """Keep an unrelated unpriceable holding from blocking a scoped browse."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for portfolio_id in ('good', 'unpriceable'):
                account = root / 'portfolio' / portfolio_id
                account.mkdir(parents=True)
                (account / 'account.yaml').write_text(
                    'apiVersion: v1\n'
                    'kind: Portfolio\n'
                    'metadata:\n'
                    f'  name: {portfolio_id}\n'
                    f'  display_name: {portfolio_id}\n'
                    'spec:\n'
                    '  broker: historical\n'
                    '  account_id: acct\n'
                    '  base_currency: USD\n',
                    encoding='utf-8',
                )
                ledger = (
                    'id,occurred_at,type,ticker,quantity,price,amount,cost_basis,'
                    'currency,fees,external_id\n'
                    'cash,2026-01-01T00:00:00+00:00,opening_cash,,,,100,,USD,0,\n'
                )
                if portfolio_id == 'unpriceable':
                    ledger += (
                        'position,2026-01-01T00:00:00+00:00,opening_position,'
                        'ZZZZ,2,,,10,USD,0,\n'
                    )
                (account / 'transactions.csv').write_text(ledger, encoding='utf-8')

            with patch(
                'py_fund_manager.browser.load_latest_daily_prices',
                return_value={},
            ) as load_prices:
                snapshots = load_portfolio_snapshots(
                    root, 'good', datetime(2026, 6, 1, tzinfo=UTC)
                )

        self.assertEqual(
            tuple(snapshot.portfolio_id for snapshot in snapshots), ('good',)
        )
        load_prices.assert_called_once()
        self.assertEqual(load_prices.call_args.args[0], set())

    def test_transaction_timestamps_are_scoped_or_unioned(self) -> None:
        """Deduplicate transaction times within the requested portfolio scope."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for portfolio_id, timestamps in (
                ('first', ('2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00')),
                ('second', ('2026-02-01T00:00:00+00:00', '2026-03-01T00:00:00+00:00')),
            ):
                account = root / 'portfolio' / portfolio_id
                account.mkdir(parents=True)
                (account / 'account.yaml').write_text(
                    'apiVersion: v1\nkind: Portfolio\nmetadata:\n'
                    f'  name: {portfolio_id}\n  display_name: {portfolio_id}\n'
                    'spec:\n  broker: historical\n  account_id: acct\n'
                    '  base_currency: USD\n',
                    encoding='utf-8',
                )
                rows = ''.join(
                    f'{index},{timestamp},deposit,,,,1,,USD,0,\n'
                    for index, timestamp in enumerate(timestamps)
                )
                (account / 'transactions.csv').write_text(
                    'id,occurred_at,type,ticker,quantity,price,amount,cost_basis,'
                    f'currency,fees,external_id\n{rows}',
                    encoding='utf-8',
                )

            scoped = load_portfolio_timestamps(root, 'first')
            union = load_portfolio_timestamps(root, None)

        self.assertEqual(
            scoped,
            (
                datetime(2026, 2, 1, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        self.assertEqual(
            union,
            (
                datetime(2026, 3, 1, tzinfo=UTC),
                datetime(2026, 2, 1, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )


class TestPortfolioBrowserApp(unittest.IsolatedAsyncioTestCase):
    """Verify the Textual master-detail interaction."""

    async def test_selecting_portfolio_updates_details(self) -> None:
        """Show the highlighted portfolio and its positions."""
        as_of = datetime(2026, 9, 1, tzinfo=UTC)
        snapshots = (
            PortfolioSnapshot(
                'first',
                'First Account',
                'historical',
                'one',
                'USD',
                as_of,
                (),
                Decimal(100),
                Decimal(0),
                Decimal(100),
                1,
                (),
            ),
            PortfolioSnapshot(
                'second',
                'Second Account',
                'historical',
                'two',
                'USD',
                as_of,
                (PositionSnapshot('AAPL', Decimal('2.5'), Decimal(75)),),
                Decimal(50),
                Decimal(75),
                Decimal(125),
                3,
                ('Prices are older than 2026-09-01 for: AAPL',),
            ),
        )
        app = PortfolioBrowserApp(snapshots)

        async with app.run_test() as pilot:
            await pilot.press('down', 'enter')
            await pilot.pause()
            identity = app.query_one('#identity', Static)
            positions = app.query_one('#positions', DataTable)
            total = app.query_one('#total', Static)

            self.assertIn('ID: second', str(identity.content))
            self.assertEqual(positions.get_row_at(0), ['AAPL', '2.5', '75.00 USD'])
            self.assertEqual(positions.get_row_at(1), ['', '', ''])
            self.assertEqual(positions.get_row_at(2), ['Cash', '', '50.00 USD'])
            self.assertEqual(str(total.content), 'Total value: 125.00 USD')
            self.assertEqual(app.title, f'second as of {as_of.isoformat()}')

            await pilot.press('backspace')
            await pilot.pause()
            self.assertTrue(app.query_one('#list-screen').display)
            self.assertEqual(app.title, f'Portfolio Browser as of {as_of.isoformat()}')

    async def test_initial_portfolio_opens_detail_then_loads_list(self) -> None:
        """Open a scoped detail directly and load every portfolio on Backspace."""
        as_of = datetime(2026, 9, 1, tzinfo=UTC)
        first = PortfolioSnapshot(
            'first',
            'First Account',
            'historical',
            'one',
            'USD',
            as_of,
            (),
            Decimal(100),
            Decimal(0),
            Decimal(100),
            1,
            (),
        )
        second = PortfolioSnapshot(
            'second',
            'Second Account',
            'historical',
            'two',
            'USD',
            as_of,
            (PositionSnapshot('AAPL', Decimal(2), Decimal(50)),),
            Decimal(50),
            Decimal(50),
            Decimal(100),
            2,
            ('Prices are older than 2026-09-01 for: AAPL',),
        )
        app = PortfolioBrowserApp(
            (second,), 'second', lambda _portfolio_id, _as_of: (first, second)
        )

        async with app.run_test() as pilot:
            self.assertFalse(app.query_one('#list-screen').display)
            self.assertTrue(app.query_one('#detail-screen').display)
            self.assertEqual(app.title, f'second as of {as_of.isoformat()}')

            await pilot.press('backspace')
            await pilot.pause()

            portfolios = app.query_one('#portfolios', DataTable)
            summary = app.query_one('#list-summary', Static)
            self.assertTrue(app.query_one('#list-screen').display)
            self.assertEqual(portfolios.row_count, 2)
            self.assertIn('ID: second', str(summary.content))
            self.assertIn('Warnings:', str(summary.content))

    async def test_deferred_load_failure_stays_in_detail_and_retries(self) -> None:
        """Preserve the detail view and retry a failed all-portfolio load."""
        as_of = datetime(2026, 9, 1, tzinfo=UTC)
        selected = PortfolioSnapshot(
            'selected',
            'Selected Account',
            'historical',
            'one',
            'USD',
            as_of,
            (),
            Decimal(100),
            Decimal(0),
            Decimal(100),
            1,
            (),
        )
        other = PortfolioSnapshot(
            'other',
            'Other Account',
            'historical',
            'two',
            'USD',
            as_of,
            (),
            Decimal(50),
            Decimal(0),
            Decimal(50),
            1,
            (),
        )
        loader = Mock(side_effect=[ValueError('missing price'), (other, selected)])
        app = PortfolioBrowserApp((selected,), 'selected', loader)

        with patch.object(app, 'notify') as notify:
            async with app.run_test() as pilot:
                await pilot.press('backspace')
                await pilot.pause()

                self.assertTrue(app.is_running)
                self.assertTrue(app.query_one('#detail-screen').display)
                self.assertEqual(app.snapshots, (selected,))
                notify.assert_called_once_with(
                    'missing price',
                    title='Unable to load portfolios',
                    severity='error',
                )

                await pilot.press('backspace')
                await pilot.pause()

                self.assertTrue(app.query_one('#list-screen').display)
                self.assertEqual(app.query_one('#portfolios', DataTable).row_count, 2)
                self.assertEqual(loader.call_count, 2)

    async def test_disappearing_selected_portfolio_preserves_detail(self) -> None:
        """Keep prior state when a deferred result omits the selected portfolio."""
        as_of = datetime(2026, 9, 1, tzinfo=UTC)
        selected = PortfolioSnapshot(
            'selected',
            'Selected Account',
            'historical',
            'one',
            'USD',
            as_of,
            (),
            Decimal(100),
            Decimal(0),
            Decimal(100),
            1,
            (),
        )
        replacement = PortfolioSnapshot(
            'replacement',
            'Replacement Account',
            'historical',
            'two',
            'USD',
            as_of,
            (),
            Decimal(50),
            Decimal(0),
            Decimal(50),
            1,
            (),
        )
        loader = Mock(return_value=(replacement,))
        app = PortfolioBrowserApp((selected,), 'selected', loader)

        with patch.object(app, 'notify') as notify:
            async with app.run_test() as pilot:
                await pilot.press('backspace')
                await pilot.pause()

                self.assertTrue(app.is_running)
                self.assertTrue(app.query_one('#detail-screen').display)
                self.assertEqual(app.snapshots, (selected,))
                self.assertEqual(app.selected_index, 0)
                notify.assert_called_once_with(
                    "portfolio 'selected' is no longer available",
                    title='Unable to load portfolios',
                    severity='error',
                )

    async def test_as_of_selection_uses_and_preserves_selected_portfolio(self) -> None:
        """Use selected portfolio timestamps without switching portfolios."""
        current = datetime(2026, 3, 1, tzinfo=UTC)
        older = datetime(2026, 1, 1, tzinfo=UTC)
        selected = PortfolioSnapshot(
            'selected',
            'Selected Account',
            'historical',
            'one',
            'USD',
            current,
            (),
            Decimal(100),
            Decimal(0),
            Decimal(100),
            1,
            (),
        )
        other = PortfolioSnapshot(
            'other',
            'Other Account',
            'historical',
            'two',
            'USD',
            current,
            (),
            Decimal(50),
            Decimal(0),
            Decimal(50),
            1,
            (),
        )

        def snapshot_loader(
            portfolio_id: str | None, as_of: datetime
        ) -> tuple[PortfolioSnapshot, ...]:
            """Return snapshots carrying the selected timestamp."""
            self.assertIn(portfolio_id, ('selected', None))
            revalued = PortfolioSnapshot(
                'selected',
                'Selected Account',
                'historical',
                'one',
                'USD',
                as_of,
                (),
                Decimal(100),
                Decimal(0),
                Decimal(100),
                1,
                (),
            )
            if portfolio_id is None:
                return (other, revalued)
            return (revalued,)

        timestamp_loader = Mock(return_value=(current, older))
        app = PortfolioBrowserApp(
            (selected,), 'selected', snapshot_loader, timestamp_loader
        )

        async with app.run_test() as pilot:
            await pilot.press('a')
            await pilot.pause()
            self.assertIsInstance(app.screen, AsOfScreen)
            self.assertEqual(
                app.screen.query_one('#as-of-options', OptionList).highlighted, 0
            )
            await pilot.press('down', 'enter')
            await pilot.pause()

            self.assertEqual(app.title, f'selected as of {older.isoformat()}')
            timestamp_loader.assert_called_once_with('selected')

            await pilot.press('backspace')
            await pilot.pause()
            self.assertEqual(app.selected_index, 1)
            self.assertEqual(app.snapshots[app.selected_index].portfolio_id, 'selected')
            await pilot.press('a')
            await pilot.pause()

            self.assertIsInstance(app.screen, AsOfScreen)
            self.assertEqual(timestamp_loader.call_args_list[-1].args, ('selected',))
            await pilot.press('enter')
            await pilot.pause()
            self.assertEqual(app.snapshots[app.selected_index].portfolio_id, 'selected')

    def test_empty_snapshot_collection_is_rejected(self) -> None:
        """Reject an app instance that could not render a list or detail."""
        with self.assertRaisesRegex(ValueError, 'at least one portfolio'):
            PortfolioBrowserApp(())
