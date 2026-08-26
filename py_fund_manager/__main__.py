"""Command-line entry point for py_fund_manager."""

import logging
import sys
from argparse import ArgumentParser, RawTextHelpFormatter
from pathlib import Path

from . import __version__
from .config import ConfigurationError, configured_data_root
from .download import Interval, download, inclusive_year_range, tickers_argument
from .log import setup_logging
from .portfolio import create_portfolio, import_opening_positions

CLI_NAME = 'py-fund-manager'

epilog = """Examples:
    python -m py_fund_manager --version
    python -m py_fund_manager -v download 2024-2025 --tickers=AAPL,MSFT
    python -m py_fund_manager download 2020 --tickers=@tickers.txt --interval=1w
    python -m py_fund_manager portfolio --create etrade-alex-roth-ira
    python -m py_fund_manager portfolio --create etrade-alex-roth-ira import-stocks stocks.csv
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


def main() -> int:
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
        '--create',
        metavar='PORTFOLIO_ID',
        help='Create a portfolio using a lowercase kebab-case ID',
    )
    portfolio_commands = portfolio_parser.add_subparsers(dest='portfolio_command')
    import_stocks_parser = portfolio_commands.add_parser(
        'import-stocks', help='Import canonical CSV holdings as opening positions'
    )
    import_stocks_parser.add_argument('stocks_file', type=Path)

    args = parser.parse_args()
    if args.version:
        print(__version__)
        return 0

    level = logging.DEBUG if args.verbose else logging.INFO
    global log
    log = setup_logging(__name__, level)

    if args.command == 'download':
        return download(args.tickers, args.years, args.interval)
    if args.command == 'portfolio':
        if args.create is None:
            portfolio_parser.error('--create PORTFOLIO_ID is required')
        try:
            portfolio_directory = create_portfolio(data_directory(), args.create)
            print(f'Created portfolio {args.create} in {portfolio_directory}')
            if args.portfolio_command == 'import-stocks':
                imported = import_opening_positions(
                    portfolio_directory, args.stocks_file
                )
                print(f'Imported {imported} opening positions from {args.stocks_file}')
        except (ConfigurationError, OSError, TypeError, ValueError) as error:
            log.log(logging.ERROR, '%s', error)
            return 1
        return 0

    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
