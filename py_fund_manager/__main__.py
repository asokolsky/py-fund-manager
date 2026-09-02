"""Command-line entry point for py_fund_manager."""

import json
import logging
import sys
from argparse import (
    ArgumentParser,
    ArgumentTypeError,
    Namespace,
    RawTextHelpFormatter,
)
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from . import __version__
from .broker import SkippedOrder, execute_rebalance_plan
from .config import ConfigurationError, configured_data_root
from .download import Interval, download, inclusive_year_range, tickers_argument
from .historical_broker import HistoricalBroker
from .log import setup_logging
from .portfolio import (
    create_portfolio,
    find_manifest,
    import_activity,
    import_opening_snapshot,
    initialize_opening_balances,
    load_strategy,
    load_transactions,
)
from .rebalance import rebalance_portfolio
from .schemas import RebalancePlan, normalize_cash_flow_amount
from .strategy import (
    analyze_strategy,
    assign_strategy,
    effective_assignment,
    load_strategy_history,
    load_strategy_revision,
    strategy_tickers,
)
from .validation import validate_data_root

CLI_NAME = 'py-fund-manager'

epilog = """Examples:
    python -m py_fund_manager --version
    python -m py_fund_manager -v download 2024-2025 --tickers=AAPL,MSFT
    python -m py_fund_manager download 2020 --tickers=@tickers.txt --interval=1w
    python -m py_fund_manager strategy show strategy.yaml
    python -m py_fund_manager strategy tickers strategy.yaml
    python -m py_fund_manager portfolio create brokerage --broker historical --account-id 1234
    python -m py_fund_manager portfolio create playground --broker historical --account-id playground --as-of 2020-01-02T08:00:00-08:00 --balance=USD:10000,AMAT:22
    python -m py_fund_manager portfolio create brokerage --broker historical --account-id 1234 --balance=@opening.csv
    python -m py_fund_manager portfolio import brokerage activity.csv
    python -m py_fund_manager portfolio strategy brokerage show
"""


def data_directory() -> Path:
    """Resolve and validate the user-configured data root."""
    directory = configured_data_root()
    if not directory.exists():
        raise ConfigurationError(f"Directory '{directory}' does not exist.")
    if not directory.is_dir():
        raise ConfigurationError(f"'{directory}' is not a directory.")
    return directory


def main() -> int:  # noqa: PLR0911 - command dispatch has explicit exit statuses.
    """Parse command-line arguments and dispatch the selected command."""
    parser = ArgumentParser(
        prog=CLI_NAME,
        description=f'{CLI_NAME} cli v{__version__}',
        formatter_class=RawTextHelpFormatter,
        epilog=epilog,
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        default=False,
        help='Tell more about what is going on',
    )
    parser.add_argument(
        '--version',
        action='store_true',
        help='Display module version and exit.',
    )
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('validate', help='Validate the complete configured data root')
    download_parser = commands.add_parser(
        'download', help='Download historical stock prices'
    )
    download_parser.add_argument(
        'years',
        type=inclusive_year_range,
        help='Year or inclusive year range in YYYY or YYYY-YYYY form',
    )
    download_parser.add_argument(
        '--tickers',
        type=tickers_argument,
        required=True,
        metavar='TICKERS|@FILE',
        help='Comma-separated ticker symbols or @ followed by a ticker file',
    )
    download_parser.add_argument(
        '--interval',
        type=Interval,
        choices=Interval,
        default=Interval.DAILY,
        help='Price-bar interval: 1h=hourly, 1d=daily, 1w=weekly (default: 1d)',
    )
    broker_parser = commands.add_parser('broker', help='Execute rebalance plans')
    broker_commands = broker_parser.add_subparsers(dest='broker', required=True)
    historical_parser = broker_commands.add_parser(
        'historical', help='Execute a plan from cached historical prices'
    )
    historical_parser.add_argument(
        'plan_file', type=Path, help='Reviewed rebalance-plan JSON file'
    )
    historical_parser.add_argument(
        '--as-of',
        type=effective_time,
        required=True,
        help='Execution timestamp for cached historical prices',
    )
    strategy_parser = commands.add_parser(
        'strategy', help='Inspect standalone strategy manifests'
    )
    strategy_commands = strategy_parser.add_subparsers(
        dest='strategy_command', required=True
    )
    show_parser = strategy_commands.add_parser(
        'show', help='Validate and summarize a strategy manifest'
    )
    show_parser.add_argument('strategy_file', type=Path)
    tickers_parser = strategy_commands.add_parser(
        'tickers', help='Print sorted ticker symbols as a comma-separated value'
    )
    tickers_parser.add_argument('strategy_file', type=Path)
    portfolio_parser = commands.add_parser(
        'portfolio', help='Create and manage portfolios'
    )
    portfolio_commands = portfolio_parser.add_subparsers(
        dest='portfolio_command', required=True
    )
    create_parser = portfolio_commands.add_parser('create', help='Create a portfolio')
    create_parser.add_argument('portfolio_id')
    create_parser.add_argument(
        '--broker',
        required=True,
        help='Broker identifier for a newly created portfolio',
    )
    create_parser.add_argument(
        '--account-id',
        required=True,
        help='Broker account identifier for a newly created portfolio',
    )
    create_parser.add_argument(
        '--as-of',
        type=effective_time,
        help='Timestamp for opening balances',
    )
    create_parser.add_argument(
        '--balance',
        type=balance_argument,
        metavar='BALANCES|@FILE',
        help='Comma-separated ASSET:VALUE balances or @ followed by a CSV file',
    )
    import_parser = portfolio_commands.add_parser(
        'import', help='Import broker activity or executions'
    )
    import_parser.add_argument('portfolio_id')
    import_parser.add_argument('source_file', type=Path)
    portfolio_strategy_parser = portfolio_commands.add_parser(
        'strategy', help='Inspect and assign portfolio strategies'
    )
    portfolio_strategy_parser.add_argument('portfolio_id')
    portfolio_strategy_commands = portfolio_strategy_parser.add_subparsers(
        dest='strategy_command', required=True
    )
    portfolio_show_parser = portfolio_strategy_commands.add_parser('show')
    portfolio_show_parser.add_argument('--as-of', type=effective_time)
    portfolio_strategy_commands.add_parser('history')
    portfolio_set_parser = portfolio_strategy_commands.add_parser('set')
    portfolio_set_parser.add_argument('strategy_id')
    portfolio_set_parser.add_argument('--as-of', type=effective_time)
    portfolio_set_parser.add_argument('--reason')
    portfolio_rebalance_parser = portfolio_commands.add_parser(
        'rebalance', help='Generate a rebalance plan'
    )
    portfolio_rebalance_parser.add_argument('portfolio_id')
    portfolio_rebalance_parser.add_argument(
        '--withdraw', type=nonnegative_amount, default=Decimal(0), dest='withdrawal'
    )
    portfolio_rebalance_parser.add_argument('--as-of', type=effective_time)

    args = parser.parse_args()
    if args.version:
        print(__version__)
        return 0

    level = logging.DEBUG if args.verbose else logging.INFO
    logger = setup_logging(__name__, level)

    if args.command == 'download':
        return _download_command(args, logger)
    if args.command == 'validate':
        return _validate_command(logger)
    if args.command == 'broker':
        return _broker_command(args, logger)
    if args.command == 'strategy':
        return _standalone_strategy_command(args, logger)
    if args.command == 'portfolio':
        return _portfolio_command(args, create_parser, logger)

    parser.print_help()
    return 0


def _download_command(args: Namespace, logger: logging.Logger) -> int:
    """Download prices using validated command-line arguments."""
    try:
        return download(args.tickers, args.years, args.interval)
    except (OSError, TypeError, ValueError) as error:
        logger.log(logging.ERROR, '%s', error)
        return 1


def _validate_command(logger: logging.Logger) -> int:
    """Validate the configured data root and print its summary."""
    try:
        summary = validate_data_root(data_directory())
    except (ConfigurationError, OSError, TypeError, ValueError) as error:
        logger.log(logging.ERROR, '%s', error)
        return 1
    print(summary.message())
    return 0


def _broker_command(args: Namespace, logger: logging.Logger) -> int:
    """Execute a reviewed plan through the selected broker adapter."""
    try:
        directory = data_directory()
        plan = load_rebalance_plan(args.plan_file)
        portfolio_directory = directory / 'portfolio' / plan.portfolio_id
        _, portfolio = find_manifest(
            portfolio_directory,
            'Portfolio',
            expected_name=plan.portfolio_id,
        )
        transactions = load_transactions(portfolio_directory / 'transactions.csv')
        broker = HistoricalBroker(args.as_of)
        result = execute_rebalance_plan(
            broker,
            portfolio,
            transactions,
            plan,
            on_order_skipped=lambda skipped: _report_skipped_order(logger, skipped),
        )
    except (OSError, TypeError, ValueError) as error:
        logger.log(logging.ERROR, '%s', error)
        return 1
    print(
        json.dumps(
            [execution.model_dump(mode='json') for execution in result.executions],
            indent=2,
        )
    )
    return 0


def _report_skipped_order(
    logger: logging.Logger,
    skipped: SkippedOrder,
) -> None:
    """Report one broker-adapter omission without corrupting command output."""
    logger.log(
        logging.WARNING,
        'Skipped %s order %s: omitted by broker adapter: %s',
        skipped.order.ticker,
        skipped.order.id,
        skipped.reason,
    )


def _standalone_strategy_command(args: Namespace, logger: logging.Logger) -> int:
    """Inspect a standalone strategy manifest."""
    try:
        strategy = load_strategy(args.strategy_file)
    except (OSError, TypeError, ValueError) as error:
        logger.log(logging.ERROR, '%s', error)
        return 1
    if args.strategy_command == 'tickers':
        print(','.join(strategy_tickers(strategy)))
    else:
        print(
            yaml.safe_dump(
                analyze_strategy(strategy).model_dump(mode='json'),
                sort_keys=False,
                explicit_end=False,
            ),
            end='',
        )
    return 0


def _portfolio_command(
    args: Namespace,
    create_parser: ArgumentParser,
    logger: logging.Logger,
) -> int:
    """Create or operate on a portfolio resource."""
    try:
        directory = data_directory()
        if args.portfolio_command == 'create':
            _create_portfolio_command(directory, args, create_parser)
        elif args.portfolio_command == 'import':
            _import_portfolio_command(directory, args)
        elif args.portfolio_command == 'strategy':
            return _strategy_command(directory, args.portfolio_id, args)
        else:
            return _rebalance_command(directory, args.portfolio_id, args)
    except (ConfigurationError, OSError, TypeError, ValueError) as error:
        logger.log(logging.ERROR, '%s', error)
        return 1
    return 0


def _create_portfolio_command(
    directory: Path,
    args: Namespace,
    create_parser: ArgumentParser,
) -> None:
    """Create a portfolio and any requested opening balances."""
    if args.as_of is not None and args.balance is None:
        create_parser.error('--as-of requires --balance')
    portfolio_directory = create_portfolio(
        directory,
        args.portfolio_id,
        broker=args.broker,
        account_id=args.account_id,
    )
    balance_message: str | None = None
    try:
        if isinstance(args.balance, Path):
            imported = import_opening_snapshot(
                portfolio_directory,
                args.balance,
                occurred_at=args.as_of,
            )
            balance_message = f'Imported {imported} opening facts from {args.balance}'
        elif args.balance is not None:
            initialized = initialize_opening_balances(
                portfolio_directory,
                args.balance,
                occurred_at=args.as_of,
            )
            balance_message = f'Initialized {initialized} opening balances'
    except OSError, TypeError, ValueError:
        _rollback_portfolio_creation(portfolio_directory)
        raise
    print(f'Created portfolio {args.portfolio_id} in {portfolio_directory}')
    if balance_message is not None:
        print(balance_message)


def _import_portfolio_command(directory: Path, args: Namespace) -> None:
    """Import confirmed activity into a portfolio ledger."""
    import_result = import_activity(
        directory / 'portfolio' / args.portfolio_id,
        args.source_file,
    )
    print(
        f'Imported {import_result.imported} activity events from '
        f'{args.source_file}; skipped {import_result.skipped}'
    )


def load_rebalance_plan(path: Path) -> RebalancePlan:
    """Load one validated rebalance plan from JSON."""
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
        return RebalancePlan.model_validate(document)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f'{path}: invalid rebalance plan: {error}') from error


def _rollback_portfolio_creation(portfolio_directory: Path) -> None:
    """Remove only artifacts written by an unsuccessful creation command."""
    for filename in ('transactions.csv', 'portfolio.yaml'):
        (portfolio_directory / filename).unlink(missing_ok=True)
    imports_directory = portfolio_directory / 'imports'
    with suppress(OSError):
        imports_directory.rmdir()
    with suppress(OSError):
        portfolio_directory.rmdir()


def effective_time(value: str) -> datetime:
    """Parse an ISO 8601 timestamp with a UTC offset for CLI arguments."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        msg = 'timestamp must use ISO 8601 format'
        raise ArgumentTypeError(msg) from error
    if parsed.tzinfo is None:
        msg = 'timestamp must include a UTC offset'
        raise ArgumentTypeError(msg)
    return parsed


def nonnegative_amount(value: str) -> Decimal:
    """Parse a nonnegative decimal amount for a CLI argument."""
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        msg = 'amount must be a decimal number'
        raise ArgumentTypeError(msg) from error
    try:
        return normalize_cash_flow_amount(amount)
    except ValueError as error:
        raise ArgumentTypeError(str(error)) from error


def balance_argument(value: str) -> dict[str, Decimal] | Path:
    """Parse inline opening balances or an @-prefixed CSV path."""
    if value.startswith('@'):
        path_value = value[1:]
        if not path_value:
            msg = '@ must be followed by an opening balance file path'
            raise ArgumentTypeError(msg)
        path = Path(path_value).expanduser()
        if not path.is_file():
            msg = f"opening balance file '{path}' does not exist or is not a file"
            raise ArgumentTypeError(msg)
        return path
    return opening_balances(value)


def opening_balances(value: str) -> dict[str, Decimal]:
    """Parse comma-separated asset balances for portfolio creation."""
    balances: dict[str, Decimal] = {}
    for entry in value.split(','):
        asset, separator, raw_amount = entry.partition(':')
        asset = asset.strip().upper()
        if not separator or not asset or not raw_amount.strip():
            msg = 'balances must use ASSET:VALUE pairs separated by commas'
            raise ArgumentTypeError(msg)
        if asset in balances:
            msg = f'duplicate opening balance for {asset}'
            raise ArgumentTypeError(msg)
        try:
            amount = Decimal(raw_amount.strip())
        except InvalidOperation as error:
            msg = f'opening balance for {asset} must be a decimal number'
            raise ArgumentTypeError(msg) from error
        if not amount.is_finite() or amount < 0:
            msg = f'opening balance for {asset} must be finite and nonnegative'
            raise ArgumentTypeError(msg)
        balances[asset] = amount
    return balances


def _strategy_command(directory: Path, portfolio_id: str, args: Namespace) -> int:
    """Dispatch a strategy command for an existing portfolio."""
    portfolio_directory = directory / 'portfolio' / portfolio_id
    if args.strategy_command == 'set':
        assignment = assign_strategy(
            directory,
            portfolio_id,
            args.strategy_id,
            args.as_of or datetime.now(UTC),
            args.reason,
        )
        print(
            yaml.safe_dump(
                assignment.model_dump(mode='json', exclude_none=True), sort_keys=False
            ),
            end='',
        )
        return 0
    history_path, _ = find_manifest(
        portfolio_directory, 'StrategyHistory', expected_name=portfolio_id
    )
    history = load_strategy_history(history_path)
    if args.strategy_command == 'history':
        for existing in history.spec.assignments:
            load_strategy_revision(directory, existing.strategy)
        print(
            yaml.safe_dump(
                history.model_dump(mode='json', by_alias=True), sort_keys=False
            ),
            end='',
        )
        return 0
    assignment = effective_assignment(history, args.as_of or datetime.now(UTC))
    strategy = load_strategy_revision(directory, assignment.strategy)
    document = {
        'assignment': assignment.model_dump(mode='json', exclude_none=True),
        'strategy': strategy.model_dump(mode='json', by_alias=True, exclude_none=True),
    }
    print(yaml.safe_dump(document, sort_keys=False), end='')
    return 0


def _rebalance_command(directory: Path, portfolio_id: str, args: Namespace) -> int:
    """Generate a JSON rebalance order plan for an existing portfolio."""
    plan = rebalance_portfolio(
        directory,
        portfolio_id,
        args.as_of or datetime.now(UTC),
        withdrawal=args.withdrawal,
    )
    print(plan.model_dump_json(indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
