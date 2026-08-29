"""Tests for the py_fund_manager command-line entry point."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, call, patch

from py_fund_manager import __main__ as cli
from py_fund_manager.download import Interval
from py_fund_manager.schemas import (
    BrokerOrder,
    Execution,
    OrderSide,
    StrategyAssignment,
    StrategyRevisionReference,
)


class TestCLI(unittest.TestCase):
    """Verify top-level CLI parsing and command dispatch."""

    def test_data_directory_uses_global_configuration(self) -> None:
        """Use the data root selected by per-user configuration."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = cli.Path(temporary_directory)
            with patch.object(cli, 'configured_data_root', return_value=data_directory):
                self.assertEqual(cli.data_directory(), data_directory)

    def test_portfolio_command_fails_without_configuration(self) -> None:
        """Fail a portfolio command when the global setting is absent."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            '--create',
            'etrade-brokerage',
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(
                cli,
                'data_directory',
                side_effect=cli.ConfigurationError('configuration is required'),
            ),
            patch.object(cli, 'create_portfolio') as create_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 1)
        create_mock.assert_not_called()

    def test_version_writes_to_stdout(self) -> None:
        """Print the exact package version to stdout and exit successfully."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, 'argv', [cli.CLI_NAME, '--version']),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), f'{cli.__version__}\n')
        self.assertEqual(stderr.getvalue(), '')

    def test_main_passes_parsed_download_arguments(self) -> None:
        """Pass parsed ticker, year, and interval values to the downloader."""
        arguments = [
            cli.CLI_NAME,
            'download',
            '2025',
            '--tickers=AAPL,MSFT',
            '--interval=1h',
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'download', return_value=0) as download_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        download_mock.assert_called_once_with(
            {'AAPL', 'MSFT'}, (2025, 2025), Interval.HOURLY
        )

    def test_historical_broker_executes_order_file(self) -> None:
        """Parse an order and print the historical broker execution as JSON."""
        submitted_at = datetime(2020, 1, 3, 15, tzinfo=UTC)
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        order = BrokerOrder(
            id='playground-order-1',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal('190.254033'),
            currency='USD',
            submitted_at=submitted_at,
        )
        execution = Execution(
            id='playground-order-1-fill-0001',
            order_id=order.id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            price=Decimal('74.35749816894531'),
            currency=order.currency,
            executed_at=executed_at,
        )
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            order_file = cli.Path(directory) / 'order.json'
            order_file.write_text(order.model_dump_json(), encoding='utf-8')
            broker = Mock()
            broker.execute_order.return_value = (execution,)
            arguments = [
                cli.CLI_NAME,
                'broker',
                'historical',
                str(order_file),
                '--as-of',
                executed_at.isoformat(),
            ]
            with (
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'HistoricalBroker', return_value=broker) as adapter,
                redirect_stdout(stdout),
            ):
                result = cli.main()

        self.assertEqual(result, 0)
        adapter.assert_called_once_with(executed_at)
        broker.execute_order.assert_called_once_with(order)
        self.assertIn('74.35749816894531', stdout.getvalue())

    def test_historical_broker_executes_order_array(self) -> None:
        """Execute a JSON order array in source order and flatten its fills."""
        submitted_at = datetime(2020, 1, 3, 15, tzinfo=UTC)
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        orders = tuple(
            BrokerOrder(
                id=f'playground-order-{index}',
                ticker=ticker,
                side=OrderSide.BUY,
                quantity=Decimal(index),
                currency='USD',
                submitted_at=submitted_at,
            )
            for index, ticker in enumerate(('AAPL', 'MSFT'), start=1)
        )
        executions = tuple(
            Execution(
                id=f'{order.id}-fill-0001',
                order_id=order.id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                price=Decimal(100),
                currency=order.currency,
                executed_at=executed_at,
            )
            for order in orders
        )
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            orders_file = cli.Path(directory) / 'orders.json'
            orders_file.write_text(
                '[' + ','.join(order.model_dump_json() for order in orders) + ']',
                encoding='utf-8',
            )
            broker = Mock()
            broker.execute_order.side_effect = (
                (execution,) for execution in executions
            )
            arguments = [
                cli.CLI_NAME,
                'broker',
                'historical',
                str(orders_file),
                '--as-of',
                executed_at.isoformat(),
            ]
            with (
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'HistoricalBroker', return_value=broker),
                redirect_stdout(stdout),
            ):
                result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            broker.execute_order.call_args_list,
            [call(order) for order in orders],
        )
        self.assertEqual(
            [execution['id'] for execution in cli.json.loads(stdout.getvalue())],
            [execution.id for execution in executions],
        )

    def test_analyze_strategy_and_extract_tickers(self) -> None:
        """Summarize a strategy or emit a download-compatible ticker list."""
        strategy_file = (
            cli.Path(__file__).parents[1] / 'sample-data/strategy/mag7/strategy.yaml'
        )
        for extra_arguments, expected in (
            ((), 'name: mag7\n'),
            (('--extract-tickers',), 'AAPL,AMZN,GOOGL,META,MSFT,NVDA,TSLA\n'),
        ):
            stdout = io.StringIO()
            arguments = [
                cli.CLI_NAME,
                'strategy',
                'analyze',
                str(strategy_file),
                *extra_arguments,
            ]
            with (
                self.subTest(arguments=extra_arguments),
                patch.object(sys, 'argv', arguments),
                redirect_stdout(stdout),
            ):
                result = cli.main()

            self.assertEqual(result, 0)
            self.assertIn(expected, stdout.getvalue())

    def test_validate_reports_complete_data_summary(self) -> None:
        """Dispatch side-effect-free data-root validation and print its summary."""
        data_directory = cli.Path('test-data')
        summary = Mock()
        summary.message.return_value = 'Validated sample data.'
        stdout = io.StringIO()
        with (
            patch.object(sys, 'argv', [cli.CLI_NAME, 'validate']),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'validate_data_root', return_value=summary
            ) as validate_mock,
            redirect_stdout(stdout),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        validate_mock.assert_called_once_with(data_directory)
        self.assertEqual(stdout.getvalue(), 'Validated sample data.\n')

    def test_create_portfolio_and_import_opening_snapshot(self) -> None:
        """Create a portfolio before importing its opening facts."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            '--create',
            'etrade-brokerage',
            'import',
            'opening.csv',
            '--as-of',
            '2020-01-02T16:00:00Z',
        ]
        data_directory = cli.Path('test-data')
        portfolio_directory = data_directory / 'portfolio' / 'etrade-brokerage'
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'create_portfolio', return_value=portfolio_directory
            ) as create_mock,
            patch.object(cli, 'import_opening_snapshot', return_value=2) as import_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        create_mock.assert_called_once_with(data_directory, 'etrade-brokerage')
        import_mock.assert_called_once_with(
            portfolio_directory,
            cli.Path('opening.csv'),
            occurred_at=datetime(2020, 1, 2, 16, tzinfo=UTC),
        )

    def test_import_portfolio_activity(self) -> None:
        """Import independently timestamped events into an existing portfolio."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'etrade-brokerage',
            'import',
            'activity.csv',
        ]
        data_directory = cli.Path('test-data')
        import_result = Mock(imported=2, skipped=1)
        stdout = io.StringIO()
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'import_activity', return_value=import_result
            ) as import_mock,
            redirect_stdout(stdout),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        import_mock.assert_called_once_with(
            data_directory / 'portfolio' / 'etrade-brokerage',
            cli.Path('activity.csv'),
        )
        self.assertIn('Imported 2 activity events', stdout.getvalue())
        self.assertIn('skipped 1', stdout.getvalue())

    def test_set_portfolio_strategy(self) -> None:
        """Parse and dispatch an effective-dated strategy assignment."""
        effective_at = datetime(2026, 9, 1, tzinfo=UTC)
        assignment = StrategyAssignment(
            id='assignment-test',
            effective_at=effective_at,
            strategy=StrategyRevisionReference(
                name='SnP500-direct', revision=f'sha256:{"a" * 64}'
            ),
            reason='Adopt direct replication',
        )
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'etrade-brokerage',
            'strategy',
            'set',
            'SnP500-direct',
            '--effective-at',
            '2026-09-01T00:00:00Z',
            '--reason',
            'Adopt direct replication',
        ]
        data_directory = cli.Path('test-data')
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'assign_strategy', return_value=assignment
            ) as assign_mock,
            redirect_stdout(io.StringIO()),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        assign_mock.assert_called_once_with(
            data_directory,
            'etrade-brokerage',
            'SnP500-direct',
            effective_at,
            'Adopt direct replication',
        )

    def test_rebalance_portfolio_with_contribution(self) -> None:
        """Parse a contribution and write the rebalance plan as JSON."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'etrade-brokerage',
            'rebalance',
            '--contribute',
            '10000.00',
            '--as-of',
            '2026-08-26T12:00:00Z',
        ]
        data_directory = cli.Path('test-data')
        plan = Mock()
        plan.model_dump_json.return_value = '{"schema_version":2}'
        stdout = io.StringIO()
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'rebalance_portfolio', return_value=plan
            ) as rebalance_mock,
            redirect_stdout(stdout),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        rebalance_mock.assert_called_once_with(
            data_directory,
            'etrade-brokerage',
            datetime(2026, 8, 26, 12, tzinfo=UTC),
            contribution=Decimal('10000.00'),
            withdrawal=Decimal(0),
        )
        self.assertEqual(stdout.getvalue(), '{"schema_version":2}\n')

    def test_rebalance_portfolio_with_withdrawal(self) -> None:
        """Parse a withdrawal and pass it to rebalance planning."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'etrade-brokerage',
            'rebalance',
            '--withdraw',
            '5000.00',
            '--as-of',
            '2026-08-26T12:00:00Z',
        ]
        data_directory = cli.Path('test-data')
        plan = Mock()
        plan.model_dump_json.return_value = '{"schema_version":2}'
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'rebalance_portfolio', return_value=plan
            ) as rebalance_mock,
            redirect_stdout(io.StringIO()),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        rebalance_mock.assert_called_once_with(
            data_directory,
            'etrade-brokerage',
            datetime(2026, 8, 26, 12, tzinfo=UTC),
            contribution=Decimal(0),
            withdrawal=Decimal('5000.00'),
        )

    def test_removed_rebalance_option_names_are_rejected(self) -> None:
        """Reject the superseded contribution and withdrawal option names."""
        for option in ('--contribution', '--withdrawal'):
            with (
                self.subTest(option=option),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                cli._parse_portfolio_action(['rebalance', option, '100'])


if __name__ == '__main__':
    unittest.main()
