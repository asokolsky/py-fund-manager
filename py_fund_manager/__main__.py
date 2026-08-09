"""Command-line entry point for py_fund_manager."""

import sys
from argparse import ArgumentParser, ArgumentTypeError, RawTextHelpFormatter
from pathlib import Path

from . import __version__
from .download import Interval, download, inclusive_year_range, tickers_argument

CLI_NAME = 'py-fund-manager'

epilog = """Examples:
    python -m py_fund_manager download 2024-2025 --tickers=AAPL,MSFT
    python -m py_fund_manager download 2020 --tickers=@tickers.txt --interval=1w
"""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FUNDS_DIRECTORY = PROJECT_ROOT / 'funds'


def existing_directory_path(path: str) -> Path:
    """Validate and return an existing directory path."""
    directory = Path(path)
    if not directory.exists():
        raise ArgumentTypeError(f"Directory '{path}' does not exist.")
    if not directory.is_dir():
        raise ArgumentTypeError(f"'{path}' is not a directory.")
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
    parser.add_argument(
        '--data',
        type=existing_directory_path,
        default=str(DEFAULT_FUNDS_DIRECTORY),
        help='Path to the funds data directory, default: funds',
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

    args = parser.parse_args()
    if args.version:
        print(__version__)
        return 0

    if args.command == 'download':
        return download(args.tickers, args.years, args.interval)

    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
