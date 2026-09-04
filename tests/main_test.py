"""Tests for the py_fund_manager command-line entry point."""

import io
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import ANY, Mock, patch

from py_fund_manager import __main__ as cli
from py_fund_manager import rebalance as rebalance_service
from py_fund_manager.broker import execution as broker_service
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
            'create',
            'brokerage',
            '--broker',
            'historical',
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

    def test_portfolio_browse_passes_scope_and_time_to_tui(self) -> None:
        """Launch the TUI portfolio browser for the requested scope and time."""
        data_root = cli.Path('/configured-data')
        as_of = datetime(2026, 9, 1, 16, tzinfo=UTC)
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'browse',
            'brokerage',
            '--as-of',
            as_of.isoformat(),
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_root),
            patch.object(cli, 'browse_portfolios') as browse,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        browse.assert_called_once_with(data_root, 'brokerage', as_of)

    def test_portfolio_browse_defers_default_timestamp_to_tui(self) -> None:
        """Let the TUI portfolio browser select an available default timestamp."""
        data_root = cli.Path('/configured-data')
        arguments = [cli.CLI_NAME, 'portfolio', 'browse']
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_root),
            patch.object(cli, 'browse_portfolios') as browse,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        browse.assert_called_once_with(data_root, None, None)

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

    def test_documented_help_output_matches_cli(self) -> None:
        """Keep every documented help block synchronized with argparse."""
        docs_directory = cli.Path(__file__).parents[1] / 'docs'
        help_block = re.compile(
            r'```shell\n'
            r'mise py-fund-manager -- (?P<arguments>[^\n]+)\n'
            r'```\n\n'
            r'```text\n'
            r'(?P<output>.*?)'
            r'```',
            re.DOTALL,
        )

        for guide in sorted(docs_directory.glob('cli*.md')):
            matches = list(help_block.finditer(guide.read_text(encoding='utf-8')))
            self.assertTrue(matches, f'{guide} has no documented help output')
            for match in matches:
                arguments = shlex.split(match.group('arguments'))
                self.assertIn(
                    arguments[-1],
                    {'-h', '--help'},
                    f'{guide} documents a non-help command as help output',
                )
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    self.subTest(guide=guide.name, arguments=arguments),
                    patch.dict(os.environ, {'COLUMNS': '80', 'LINES': '24'}),
                    patch.object(sys, 'argv', [cli.CLI_NAME, *arguments]),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as exit_context,
                ):
                    cli.main()

                self.assertEqual(exit_context.exception.code, 0)
                self.assertEqual(stderr.getvalue(), '')
                self.assertEqual(match.group('output'), stdout.getvalue())

    def test_cash_flow_amount_rejects_sub_cent_precision(self) -> None:
        """Reject withdrawal values smaller than one cent."""
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
        with tempfile.TemporaryDirectory() as directory:
            source = cli.Path(directory) / 'opening.csv'
            source.write_text('asset,amount\nUSD,1\n', encoding='utf-8')
            self.assertEqual(cli.balance_argument(f'@{source}'), source)
            with patch.dict(os.environ, {'HOME': directory}):
                self.assertEqual(cli.balance_argument('@~/opening.csv'), source)
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

    def test_download_reports_expected_errors(self) -> None:
        """Return a failure status when downloading raises an expected error."""
        arguments = [
            cli.CLI_NAME,
            'download',
            '2025',
            '--tickers=AAPL',
        ]
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'download', side_effect=ValueError('download failed')),
        ):
            result = cli.main()

        self.assertEqual(result, 1)

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
            broker.prepare_order.side_effect = lambda order: order
            execution_result = Mock(executions=(execution,), skipped_orders=())
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
        execute.assert_called_once_with(
            broker,
            portfolio,
            transactions,
            plan,
            on_order_skipped=ANY,
        )
        self.assertEqual(
            stdout.getvalue(),
            json.dumps([execution.model_dump(mode='json')], indent=2) + '\n',
        )

    def test_historical_broker_rounds_execution_prices_to_market_precision(
        self,
    ) -> None:
        """Round standard and sub-dollar fills to their market increments."""
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        broker = cli.HistoricalBroker(executed_at)
        order = BrokerOrder(
            id='order-1',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal(1),
            currency='USD',
            submitted_at=executed_at,
        )

        for raw_price, expected_price in (
            (Decimal('74.355'), Decimal('74.36')),
            (Decimal('0.12345'), Decimal('0.1235')),
        ):
            with (
                self.subTest(raw_price=raw_price),
                patch(
                    'py_fund_manager.broker.historical.load_latest_daily_prices',
                    return_value={'AAPL': Mock(price=raw_price)},
                ),
            ):
                execution = broker.execute_order(order)[0]

            self.assertEqual(execution.price, expected_price)

    def test_historical_broker_defaults_to_etrade_quantity_precision(self) -> None:
        """Round planned quantities down to E*TRADE's three-decimal precision."""
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        order = BrokerOrder(
            id='order-1',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal('1.1239'),
            currency='USD',
            submitted_at=executed_at,
        )

        prepared = cli.HistoricalBroker(executed_at).prepare_order(order)

        self.assertIsInstance(prepared, BrokerOrder)
        assert isinstance(prepared, BrokerOrder)
        self.assertEqual(prepared.quantity, Decimal('1.123'))

    def test_historical_broker_quantity_precision_is_configurable(self) -> None:
        """Support another broker's share-quantity precision when configured."""
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        order = BrokerOrder(
            id='order-1',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal('1.12349'),
            currency='USD',
            submitted_at=executed_at,
        )

        prepared = cli.HistoricalBroker(
            executed_at,
            quantity_precision=4,
        ).prepare_order(order)

        self.assertIsInstance(prepared, BrokerOrder)
        assert isinstance(prepared, BrokerOrder)
        self.assertEqual(prepared.quantity, Decimal('1.1234'))

    def test_historical_broker_adapts_sells_and_omits_dust(self) -> None:
        """Fund sells, preserve liquidations, and omit unsupported dust."""
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        broker = cli.HistoricalBroker(executed_at)
        sell = BrokerOrder(
            id='order-1',
            ticker='AAPL',
            side=OrderSide.SELL,
            quantity=Decimal('1.1231'),
            maximum_quantity=Decimal(2),
            currency='USD',
            submitted_at=executed_at,
        )
        liquidation = BrokerOrder(
            id='order-2',
            ticker='AAPL',
            side=OrderSide.SELL,
            quantity=Decimal('1.2345'),
            maximum_quantity=Decimal('1.2345'),
            close_position=True,
            currency='USD',
            submitted_at=executed_at,
        )
        dust = BrokerOrder(
            id='order-3',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal('0.000003'),
            currency='USD',
            submitted_at=executed_at,
        )
        tiny_holding = BrokerOrder(
            id='order-5',
            ticker='AAPL',
            side=OrderSide.SELL,
            quantity=Decimal('0.000003'),
            maximum_quantity=Decimal('0.000004'),
            currency='USD',
            submitted_at=executed_at,
        )
        capped_sell = BrokerOrder(
            id='order-4',
            ticker='AAPL',
            side=OrderSide.SELL,
            quantity=Decimal('1.2343'),
            maximum_quantity=Decimal('1.2345'),
            currency='USD',
            submitted_at=executed_at,
        )

        prepared_sell = broker.prepare_order(sell)
        prepared_liquidation = broker.prepare_order(liquidation)
        prepared_capped_sell = broker.prepare_order(capped_sell)
        skipped_dust = broker.prepare_order(dust)
        skipped_tiny_holding = broker.prepare_order(tiny_holding)

        self.assertIsInstance(prepared_sell, BrokerOrder)
        self.assertIsInstance(prepared_liquidation, BrokerOrder)
        self.assertIsInstance(prepared_capped_sell, BrokerOrder)
        assert isinstance(prepared_sell, BrokerOrder)
        assert isinstance(prepared_liquidation, BrokerOrder)
        assert isinstance(prepared_capped_sell, BrokerOrder)
        self.assertEqual(prepared_sell.quantity, Decimal('1.124'))
        self.assertEqual(prepared_liquidation.quantity, Decimal('1.2345'))
        self.assertEqual(prepared_capped_sell.quantity, Decimal('1.234'))
        self.assertEqual(prepared_capped_sell.quantity.as_tuple().exponent, -3)
        self.assertIsInstance(skipped_dust, cli.SkippedOrder)
        assert isinstance(skipped_dust, cli.SkippedOrder)
        self.assertIn('cannot be represented', skipped_dust.reason)
        self.assertIsInstance(skipped_tiny_holding, cli.SkippedOrder)
        assert isinstance(skipped_tiny_holding, cli.SkippedOrder)
        self.assertIn('holding 0.000004', skipped_tiny_holding.reason)

    def test_skipped_order_is_reported_before_later_execution_failure(self) -> None:
        """Report an omission even when final cash validation later fails."""
        executed_at = datetime(2020, 1, 3, 21, tzinfo=UTC)
        dust = BrokerOrder(
            id='order-1',
            ticker='AAPL',
            side=OrderSide.BUY,
            quantity=Decimal('0.000003'),
            currency='USD',
            submitted_at=executed_at,
        )
        executable = BrokerOrder(
            id='order-2',
            ticker='MSFT',
            side=OrderSide.SELL,
            quantity=Decimal(1),
            maximum_quantity=Decimal(2),
            currency='USD',
            submitted_at=executed_at,
        )
        skipped = cli.SkippedOrder(dust, 'adapter-specific reason')
        execution = Execution(
            id='execution-1',
            order_id=executable.id,
            ticker=executable.ticker,
            side=executable.side,
            quantity=executable.quantity,
            price=Decimal(1),
            currency=executable.currency,
            executed_at=executed_at,
        )
        adapter = Mock()
        adapter.prepare_order.side_effect = (skipped, executable)
        adapter.execute_order.return_value = (execution,)
        plan = Mock(
            orders=(Mock(), Mock()),
            valuation=Mock(as_of=executed_at, withdrawal=Decimal(10)),
        )
        report = Mock()
        fact = Mock(occurred_at=executed_at)

        with (
            patch.object(broker_service, '_validate_plan_inputs', return_value={}),
            patch.object(
                broker_service,
                '_broker_order',
                side_effect=(dust, executable),
            ),
            patch.object(broker_service, 'execution_transaction', return_value=fact),
            patch.object(broker_service, 'validate_transaction_ledger'),
            patch.object(
                broker_service,
                'derive_portfolio_state',
                return_value=({}, Decimal(0)),
            ),
            self.assertRaisesRegex(ValueError, 'insufficient cash'),
        ):
            broker_service.execute_rebalance_plan(
                adapter,
                Mock(),
                [],
                plan,
                on_order_skipped=report,
            )

        report.assert_called_once_with(skipped)

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
        """Create real portfolio artifacts from an @-prefixed opening snapshot."""
        with tempfile.TemporaryDirectory() as directory:
            data_directory = cli.Path(directory)
            source = data_directory / 'opening.csv'
            source.write_text(
                'asset,quantity,amount,cost_basis\nUSD,,10000,\nAMAT,22,,4400\n',
                encoding='utf-8',
            )
            arguments = [
                cli.CLI_NAME,
                'portfolio',
                'create',
                'brokerage',
                '--broker',
                'historical',
                '--account-id',
                'brokerage-123',
                '--as-of',
                '2020-01-02T16:00:00Z',
                f'--balance=@{source}',
            ]
            with (
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'data_directory', return_value=data_directory),
                redirect_stdout(io.StringIO()),
            ):
                result = cli.main()

            portfolio_directory = data_directory / 'portfolio/brokerage'
            _, portfolio = cli.find_manifest(portfolio_directory, 'Portfolio')
            transactions = cli.load_transactions(
                portfolio_directory / 'transactions.csv'
            )
            preserved = portfolio_directory / 'imports/opening.csv'

            self.assertEqual(result, 0)
            self.assertEqual(portfolio.spec.broker, 'historical')
            self.assertEqual(portfolio.spec.account_id, 'brokerage-123')
            self.assertEqual(transactions[0].amount, Decimal(10000))
            self.assertEqual(transactions[1].ticker, 'AMAT')
            self.assertEqual(transactions[1].quantity, Decimal(22))
            self.assertEqual(transactions[1].cost_basis, Decimal(4400))
            self.assertEqual(
                transactions[0].occurred_at, datetime(2020, 1, 2, 16, tzinfo=UTC)
            )
            self.assertEqual(preserved.read_text(), source.read_text())

    def test_create_portfolio_requires_broker_and_account_id(self) -> None:
        """Reject creation when either required account identity is absent."""
        data_directory = cli.Path('test-data')
        for option, value in (
            ('--broker', 'historical'),
            ('--account-id', 'brokerage-123'),
        ):
            arguments = [
                cli.CLI_NAME,
                'portfolio',
                'create',
                'brokerage',
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
        """Create real portfolio artifacts from inline opening balances."""
        with tempfile.TemporaryDirectory() as directory:
            data_directory = cli.Path(directory)
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
            with (
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'data_directory', return_value=data_directory),
                redirect_stdout(io.StringIO()),
            ):
                result = cli.main()

            portfolio_directory = data_directory / 'portfolio/playground'
            _, portfolio = cli.find_manifest(portfolio_directory, 'Portfolio')
            transactions = cli.load_transactions(
                portfolio_directory / 'transactions.csv'
            )

            self.assertEqual(result, 0)
            self.assertEqual(portfolio.spec.broker, 'historical')
            self.assertEqual(portfolio.spec.account_id, 'playground')
            self.assertEqual(transactions[0].amount, Decimal(10000))
            self.assertEqual(transactions[1].ticker, 'AMAT')
            self.assertEqual(transactions[1].quantity, Decimal(22))
            self.assertEqual(
                transactions[0].occurred_at,
                datetime.fromisoformat('2020-01-02T08:00:00-08:00'),
            )
            self.assertFalse((portfolio_directory / 'imports').exists())

    def test_create_portfolio_rolls_back_invalid_balance_file(self) -> None:
        """Preserve scaffolding without leaving a partial portfolio resource."""
        with tempfile.TemporaryDirectory() as directory:
            data_directory = cli.Path(directory)
            portfolio_directory = data_directory / 'portfolio/playground'
            portfolio_directory.mkdir(parents=True)
            readme = portfolio_directory / 'README.md'
            readme.write_text('# Playground\n', encoding='utf-8')
            arguments = [
                cli.CLI_NAME,
                'portfolio',
                'create',
                'playground',
                '--broker',
                'historical',
                '--account-id',
                'playground',
                f'--balance=@{data_directory / "invalid.csv"}',
            ]
            (data_directory / 'invalid.csv').write_text(
                'asset,amount\nEUR,100\n', encoding='utf-8'
            )
            stdout = io.StringIO()
            with (
                patch.object(sys, 'argv', arguments),
                patch.object(cli, 'data_directory', return_value=data_directory),
                redirect_stdout(stdout),
                redirect_stderr(io.StringIO()),
            ):
                result = cli.main()

            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(readme.read_text(), '# Playground\n')
            self.assertFalse((portfolio_directory / 'portfolio.yaml').exists())
            self.assertFalse((portfolio_directory / 'transactions.csv').exists())
            self.assertFalse((portfolio_directory / 'imports').exists())

    def test_legacy_portfolio_shape_is_rejected(self) -> None:
        """Reject a portfolio ID where the command subparser is required."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'brokerage',
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
            'brokerage',
            'activity.csv',
        ]
        data_directory = cli.Path('test-data')
        import_result = Mock(imported=2, skipped=1)
        stdout = io.StringIO()
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                cli, 'import_broker_activity', return_value=import_result
            ) as import_mock,
            redirect_stdout(stdout),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        import_mock.assert_called_once_with(
            data_directory / 'portfolio' / 'brokerage',
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
            'brokerage',
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
            'brokerage',
            'SnP500-direct',
            effective_at,
            'Adopt direct replication',
        )

    def test_rebalance_portfolio_with_withdrawal(self) -> None:
        """Parse a withdrawal and pass it to rebalance planning."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'rebalance',
            'brokerage',
            '--withdraw',
            '5000.00',
            '--as-of',
            '2026-08-26T12:00:00Z',
            '--allow-stale-prices',
        ]
        data_directory = cli.Path('test-data')
        plan = Mock()
        plan.model_dump_json.return_value = '{"schema_version":1}'
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
            'brokerage',
            datetime(2026, 8, 26, 12, tzinfo=UTC),
            withdrawal=Decimal('5000.00'),
            allow_stale_prices=True,
        )

    def test_rebalance_stdout_remains_json_after_successful_refresh(self) -> None:
        """Send refresh progress to stderr so broker plan stdout stays parseable."""
        arguments = [
            cli.CLI_NAME,
            'portfolio',
            'rebalance',
            'brokerage',
            '--as-of',
            '2026-08-26T12:00:00Z',
        ]
        data_directory = cli.Path('test-data')
        portfolio = Mock()
        portfolio.spec.base_currency = 'USD'
        strategy = Mock(target_weights={'AAPL': Decimal(1)})
        plan = Mock()
        plan.model_dump_json.return_value = '{"schema_version": 1}'

        def successful_refresh(
            *_: object, progress_stream: io.StringIO, **__: object
        ) -> int:
            print('Wrote 1 rows to test-prices/data.parquet', file=progress_stream)
            return 0

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, 'argv', arguments),
            patch.object(cli, 'data_directory', return_value=data_directory),
            patch.object(
                rebalance_service, 'load_directory_manifests', return_value={}
            ),
            patch.object(
                rebalance_service,
                'find_manifest_in',
                side_effect=[
                    (cli.Path('portfolio.yaml'), portfolio),
                    (cli.Path('history.yaml'), Mock()),
                ],
            ),
            patch.object(rebalance_service, 'load_transactions', return_value=[]),
            patch.object(
                rebalance_service, 'effective_assignment', return_value=Mock()
            ),
            patch.object(
                rebalance_service, 'load_strategy_revision', return_value=strategy
            ),
            patch.object(
                rebalance_service,
                'derive_portfolio_state',
                return_value=({'AAPL': Decimal(1)}, Decimal(0)),
            ),
            patch.object(rebalance_service, 'download', side_effect=successful_refresh),
            patch.object(
                rebalance_service,
                'load_latest_daily_prices',
                return_value={'AAPL': Mock()},
            ),
            patch.object(
                rebalance_service, 'validate_price_freshness', return_value=()
            ),
            patch.object(rebalance_service, 'plan_rebalance', return_value=plan),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {'schema_version': 1})
        self.assertIn('Wrote 1 rows', stderr.getvalue())

    def test_removed_rebalance_option_names_are_rejected(self) -> None:
        """Reject removed contribution and superseded withdrawal option names."""
        for option in ('--contribute', '--contribution', '--withdrawal'):
            with (
                self.subTest(option=option),
                patch.object(
                    sys,
                    'argv',
                    [
                        cli.CLI_NAME,
                        'portfolio',
                        'rebalance',
                        'brokerage',
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
