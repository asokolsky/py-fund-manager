"""Tests for the py_fund_manager command-line entry point."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

from py_fund_manager import __main__ as cli
from py_fund_manager.download import Interval
from py_fund_manager.schemas import (
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
            'create',
            'etrade-brokerage',
            '--broker',
            'etrade',
            '--account-id',
            'brokerage-123',
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

    def test_cash_flow_amount_rejects_sub_cent_precision(self) -> None:
        """Reject contribution and withdrawal values smaller than one cent."""
        self.assertEqual(cli.nonnegative_amount('100.00'), Decimal('100.00'))
        self.assertEqual(cli.nonnegative_amount('100.000'), Decimal('100.00'))
        self.assertEqual(cli.nonnegative_amount('0E-100'), Decimal('0.00'))
        self.assertEqual(
            cli.nonnegative_amount('999999999999999999'),
            Decimal('999999999999999999.00'),
        )
        with self.assertRaisesRegex(
            cli.ArgumentTypeError, 'fractions smaller than one cent'
        ):
            cli.nonnegative_amount('100.005')
        with self.assertRaisesRegex(cli.ArgumentTypeError, '18-digit limit'):
            cli.nonnegative_amount('1E+18')

    def test_opening_balances_parse_assets_and_reject_ambiguity(self) -> None:
        """Normalize inline balances and reject duplicate or malformed assets."""
        self.assertEqual(cli.balance_argument('@opening.csv'), cli.Path('opening.csv'))
        self.assertEqual(
            cli.opening_balances('usd:10000, AMAT:22'),
            {'USD': Decimal(10000), 'AMAT': Decimal(22)},
        )
        with self.assertRaisesRegex(cli.ArgumentTypeError, 'duplicate.*USD'):
            cli.opening_balances('USD:1,usd:2')
        with self.assertRaisesRegex(cli.ArgumentTypeError, 'ASSET:VALUE'):
            cli.opening_balances('USD')

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

    def test_historical_broker_executes_validated_plan(self) -> None:
        """Execute a plan against its portfolio ledger and print fills as JSON."""
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        execution = Execution(
            id='playground-order-1-fill-0001',
            order_id='playground-order-1',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal('190.254033'),
            price=Decimal('74.35749816894531'),
            currency='USD',
            executed_at=executed_at,
        )
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            data_root = cli.Path(directory)
            plan_file = data_root / 'rebalance-plan.json'
            plan_file.write_text('{}', encoding='utf-8')
            plan = Mock(portfolio_id='playground')
            portfolio = Mock()
            transactions = [Mock()]
            broker = Mock()
            execution_result = Mock(executions=(execution,))
            arguments = [
                cli.CLI_NAME,
                'broker',
                'historical',
                str(plan_file),
                '--as-of',
                executed_at.isoformat(),
            ]
            with (
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'data_directory', return_value=data_root),
                patch.object(cli, 'load_rebalance_plan', return_value=plan),
                patch.object(
                    cli,
                    'find_manifest',
                    return_value=(data_root / 'renamed.yaml', portfolio),
                ) as find_manifest,
                patch.object(
                    cli, 'load_transactions', return_value=transactions
                ) as load_transactions,
                patch.object(cli, 'HistoricalBroker', return_value=broker) as adapter,
                patch.object(
                    cli, 'execute_rebalance_plan', return_value=execution_result
                ) as execute,
                redirect_stdout(stdout),
            ):
                cli_result = cli.main()

        self.assertEqual(cli_result, 0)
        adapter.assert_called_once_with(executed_at)
        find_manifest.assert_called_once_with(
            data_root / 'portfolio/playground',
            'Portfolio',
            expected_name='playground',
        )
        load_transactions.assert_called_once_with(
            data_root / 'portfolio/playground/transactions.csv'
        )
        execute.assert_called_once_with(broker, portfolio, transactions, plan)
        self.assertIn('74.35749816894531', stdout.getvalue())

    def test_show_strategy_and_list_tickers(self) -> None:
        """Summarize a strategy or emit a download-compatible ticker list."""
        strategy_file = (
            cli.Path(__file__).parents[1] / 'sample-data/strategy/mag7/strategy.yaml'
        )
        for subcommand, expected in (
            ('show', 'name: mag7\n'),
            ('tickers', 'AAPL,AMZN,GOOGL,META,MSFT,NVDA,TSLA\n'),
        ):
            stdout = io.StringIO()
            arguments = [
                cli.CLI_NAME,
                'strategy',
                subcommand,
                str(strategy_file),
            ]
            with (
                self.subTest(subcommand=subcommand),
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

    def test_create_portfolio_from_opening_snapshot(self) -> None:
        """Create a portfolio from an @-prefixed opening snapshot."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'create',
            'etrade-brokerage',
            '--broker',
            'etrade',
            '--account-id',
            'brokerage-123',
            '--as-of',
            '2020-01-02T16:00:00Z',
            '--balance=@opening.csv',
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
        create_mock.assert_called_once_with(
            data_directory,
            'etrade-brokerage',
            broker='etrade',
            account_id='brokerage-123',
        )
        import_mock.assert_called_once_with(
            portfolio_directory,
            cli.Path('opening.csv'),
            occurred_at=datetime(2020, 1, 2, 16, tzinfo=UTC),
        )

    def test_create_portfolio_requires_broker_and_account_id(self) -> None:
        """Reject creation when either required account identity is absent."""
        data_directory = cli.Path('test-data')
        for option, value in (
            ('--broker', 'etrade'),
            ('--account-id', 'brokerage-123'),
        ):
            arguments = [
                cli.CLI_NAME,
                'portfolio',
                'create',
                'etrade-brokerage',
                option,
                value,
            ]
            with (
                self.subTest(option=option),
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'data_directory', return_value=data_directory),
                patch.object(cli, 'create_portfolio') as create_mock,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                cli.main()

            create_mock.assert_not_called()

    def test_create_portfolio_with_inline_opening_balances(self) -> None:
        """Create a portfolio and initialize its ledger without an import file."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'create',
            'playground',
            '--broker',
            'historical',
            '--account-id',
            'playground',
            '--as-of',
            '2020-01-02T08:00:00-08:00',
            '--balance=USD:10000,AMAT:22',
        ]
        data_directory = cli.Path('test-data')
        portfolio_directory = data_directory / 'portfolio/playground'
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'create_portfolio', return_value=portfolio_directory
            ) as create_mock,
            patch.object(
                cli, 'initialize_opening_balances', return_value=2
            ) as initialize_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        create_mock.assert_called_once_with(
            data_directory,
            'playground',
            broker='historical',
            account_id='playground',
        )
        initialize_mock.assert_called_once_with(
            portfolio_directory,
            {'USD': Decimal(10000), 'AMAT': Decimal(22)},
            occurred_at=datetime.fromisoformat('2020-01-02T08:00:00-08:00'),
        )

    def test_legacy_portfolio_shape_is_rejected(self) -> None:
        """Reject a portfolio ID where the command subparser is required."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'etrade-brokerage',
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=cli.Path('test-data')),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            cli.main()

    def test_import_portfolio_activity(self) -> None:
        """Import independently timestamped events into an existing portfolio."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'import',
            'etrade-brokerage',
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
            'strategy',
            'etrade-brokerage',
            'set',
            'SnP500-direct',
            '--as-of',
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
            'rebalance',
            'etrade-brokerage',
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
            'rebalance',
            'etrade-brokerage',
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
                patch.object(
                    sys,
                    'argv',
                    [
                        cli.CLI_NAME,
                        'portfolio',
                        'rebalance',
                        'etrade-brokerage',
                        option,
                        '100',
                    ],
                ),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                cli.main()


if __name__ == '__main__':
    unittest.main()
