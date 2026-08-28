"""Command-line entry point for py_fund_manager."""

import logging
import sys
from argparse import (
    REMAINDER,
    ArgumentParser,
    ArgumentTypeError,
    Namespace,
    RawTextHelpFormatter,
)
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from . import __version__
from .config import ConfigurationError, configured_data_root
from .download import Interval, download, inclusive_year_range, tickers_argument
from .log import setup_logging
from .portfolio import create_portfolio, find_manifest, import_opening_positions
from .rebalance import rebalance_portfolio
from .strategy import (
    assign_strategy,
    effective_assignment,
    load_strategy_history,
    load_strategy_revision,
)
from .validation import validate_data_root

CLI_NAME = 'py-fund-manager'

epilog = """Examples:
    python -m py_fund_manager --version
    python -m py_fund_manager -v download 2024-2025 --tickers=AAPL,MSFT
    python -m py_fund_manager download 2020 --tickers=@tickers.txt --interval=1w
    python -m py_fund_manager portfolio --create etrade-brokerage
    python -m py_fund_manager portfolio --create etrade-brokerage import-stocks stocks.csv
    python -m py_fund_manager portfolio etrade-brokerage strategy show
"""

log: logging.Logger | None = None


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
    portfolio_parser = commands.add_parser(
        'portfolio', help='Create and manage portfolios'
    )
    portfolio_parser.add_argument(
        'portfolio_id',
        nargs='?',
        help='Existing portfolio ID for management commands',
    )
    portfolio_parser.add_argument(
        '--create',
        metavar='PORTFOLIO_ID',
        help='Create a portfolio using a lowercase kebab-case ID',
    )
    portfolio_parser.add_argument(
        'portfolio_arguments',
        nargs=REMAINDER,
        help='import-stocks or strategy operation and its arguments',
    )

    args = parser.parse_args()
    if args.version:
        print(__version__)
        return 0

    level = logging.DEBUG if args.verbose else logging.INFO
    global log
    log = setup_logging(__name__, level)

    if args.command == 'download':
        return download(args.tickers, args.years, args.interval)
    if args.command == 'validate':
        try:
            summary = validate_data_root(data_directory())
        except (ConfigurationError, OSError, TypeError, ValueError) as error:
            log.log(logging.ERROR, '%s', error)
            return 1
        print(summary.message())
        return 0
    if args.command == 'portfolio':
        try:
            directory = data_directory()
            if args.create is not None:
                action_arguments = (
                    [] if args.portfolio_id is None else [args.portfolio_id]
                ) + args.portfolio_arguments
                action = (
                    None
                    if not action_arguments
                    else _parse_create_action(action_arguments)
                )
                portfolio_directory = create_portfolio(directory, args.create)
                print(f'Created portfolio {args.create} in {portfolio_directory}')
                if action is not None:
                    imported = import_opening_positions(
                        portfolio_directory, action.stocks_file
                    )
                    print(
                        f'Imported {imported} opening positions from {action.stocks_file}'
                    )
            elif args.portfolio_id is not None:
                action = _parse_portfolio_action(args.portfolio_arguments)
                if action.portfolio_command == 'strategy':
                    result = _strategy_command(directory, args.portfolio_id, action)
                else:
                    result = _rebalance_command(directory, args.portfolio_id, action)
                return result
            else:
                portfolio_parser.error(
                    '--create PORTFOLIO_ID or an existing portfolio ID is required'
                )
        except (ConfigurationError, OSError, TypeError, ValueError) as error:
            log.log(logging.ERROR, '%s', error)
            return 1
        return 0

    parser.print_help()
    return 0


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
    if not amount.is_finite() or amount < 0:
        msg = 'amount must be a finite nonnegative decimal number'
        raise ArgumentTypeError(msg)
    return amount


def _parse_create_action(arguments: list[str]) -> Namespace:
    """Parse an optional action performed immediately after portfolio creation."""
    parser = ArgumentParser(prog=f'{CLI_NAME} portfolio --create PORTFOLIO_ID')
    commands = parser.add_subparsers(dest='command', required=True)
    import_parser = commands.add_parser('import-stocks')
    import_parser.add_argument('stocks_file', type=Path)
    return parser.parse_args(arguments)


def _parse_portfolio_action(arguments: list[str]) -> Namespace:
    """Parse an operation for an existing portfolio."""
    parser = ArgumentParser(prog=f'{CLI_NAME} portfolio PORTFOLIO_ID')
    commands = parser.add_subparsers(dest='portfolio_command', required=True)
    strategy_parser = commands.add_parser('strategy')
    strategy_commands = strategy_parser.add_subparsers(
        dest='strategy_command', required=True
    )
    show_parser = strategy_commands.add_parser('show')
    show_parser.add_argument('--effective-at', type=effective_time)
    strategy_commands.add_parser('history')
    set_parser = strategy_commands.add_parser('set')
    set_parser.add_argument('strategy_id')
    set_parser.add_argument('--effective-at', type=effective_time)
    set_parser.add_argument('--reason')
    rebalance_parser = commands.add_parser('rebalance')
    cash_flow = rebalance_parser.add_mutually_exclusive_group()
    cash_flow.add_argument(
        '--contribute', type=nonnegative_amount, default=Decimal(0), dest='contribution'
    )
    cash_flow.add_argument(
        '--withdraw', type=nonnegative_amount, default=Decimal(0), dest='withdrawal'
    )
    rebalance_parser.add_argument('--as-of', type=effective_time)
    return parser.parse_args(arguments)


def _strategy_command(directory: Path, portfolio_id: str, args: Namespace) -> int:
    """Dispatch a strategy command for an existing portfolio."""
    portfolio_directory = directory / 'portfolio' / portfolio_id
    if args.strategy_command == 'set':
        assignment = assign_strategy(
            directory,
            portfolio_id,
            args.strategy_id,
            args.effective_at or datetime.now(UTC),
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
    assignment = effective_assignment(history, args.effective_at or datetime.now(UTC))
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
        contribution=args.contribution,
        withdrawal=args.withdrawal,
    )
    print(plan.model_dump_json(indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
