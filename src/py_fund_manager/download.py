"""Download and store historical stock prices."""

import re
import sys
import tempfile
from argparse import ArgumentTypeError
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STOCKS_DIRECTORY = PROJECT_ROOT / 'stocks-by-ticker'
TICKER_PATTERN = re.compile(r'^[A-Z0-9.^=_-]+$')
YEAR_RANGE_PATTERN = re.compile(r'^(\d{4})(?:-(\d{4}))?$')


class Interval(StrEnum):
    """Supported historical price-bar intervals."""

    HOURLY = '1h'
    DAILY = '1d'
    WEEKLY = '1w'

    @property
    def yfinance_value(self) -> str:
        """Return the interval spelling expected by yfinance."""
        if self is Interval.WEEKLY:
            return '1wk'
        return self.value


def comma_separated_tickers(value: str) -> set[str]:
    """Parse and normalize a comma-separated ticker list."""
    # Download order is intentionally unspecified, so a set models the unique
    # ticker symbols without retaining meaningless input order.
    tickers = {item.strip().upper() for item in value.split(',')}
    if not tickers or any(not ticker for ticker in tickers):
        message = 'Provide one or more comma-separated tickers.'
        raise ArgumentTypeError(message)
    invalid = [ticker for ticker in tickers if not TICKER_PATTERN.fullmatch(ticker)]
    if invalid:
        raise ArgumentTypeError(f"Invalid ticker: '{invalid[0]}'.")
    return tickers


def tickers_argument(value: str) -> set[str]:
    """Parse comma-separated tickers or load one ticker per line from an @file."""
    if not value.startswith('@'):
        return comma_separated_tickers(value)

    path_value = value[1:]
    if not path_value:
        message = "A ticker file path must follow '@'."
        raise ArgumentTypeError(message)
    path = Path(path_value).expanduser()
    if not path.exists():
        raise ArgumentTypeError(f"Ticker file '{path}' does not exist.")
    if not path.is_file():
        raise ArgumentTypeError(f"Ticker path '{path}' is not a file.")

    tickers: set[str] = set()
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError) as error:
        raise ArgumentTypeError(f"Cannot read ticker file '{path}': {error}") from error

    for line_number, line in enumerate(lines, start=1):
        ticker = line.strip().upper()
        if not ticker or ticker.startswith('#'):
            continue
        if not TICKER_PATTERN.fullmatch(ticker):
            message = f"Invalid ticker '{ticker}' in '{path}' at line {line_number}."
            raise ArgumentTypeError(message)
        tickers.add(ticker)

    if not tickers:
        raise ArgumentTypeError(f"Ticker file '{path}' contains no ticker symbols.")
    return tickers


def inclusive_year_range(value: str) -> tuple[int, int]:
    """Parse one year or an inclusive year range."""
    match = YEAR_RANGE_PATTERN.fullmatch(value)
    if match is None:
        message = "Year must use 'YYYY' or the range format 'YYYY-YYYY'."
        raise ArgumentTypeError(message)
    start_year = int(match.group(1))
    end_year = int(match.group(2) or start_year)
    current_year = datetime.now(tz=UTC).year
    if start_year > end_year:
        message = 'The first year must not be after the second year.'
        raise ArgumentTypeError(message)
    if start_year < 1900 or end_year > current_year:
        raise ArgumentTypeError(
            f'Years must be between 1900 and {current_year}, inclusive.'
        )
    return start_year, end_year


def normalize_history(
    history: pd.DataFrame,
    interval: Interval = Interval.DAILY,
) -> pd.DataFrame:
    """Convert a yfinance history frame to the documented Parquet schema."""
    required_columns = {'Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume'}
    missing_columns = required_columns.difference(history.columns)
    if missing_columns:
        missing = ', '.join(sorted(missing_columns))
        raise ValueError(f'Market data is missing required columns: {missing}')

    time_column = 'timestamp' if interval is Interval.HOURLY else 'date'
    time_values = (
        history.index.to_pydatetime()
        if interval is Interval.HOURLY
        else history.index.date
    )
    normalized = pd.DataFrame(
        {
            time_column: pd.Series(time_values, dtype='object'),
            'open': pd.to_numeric(history['Open'], errors='coerce').to_numpy(),
            'high': pd.to_numeric(history['High'], errors='coerce').to_numpy(),
            'low': pd.to_numeric(history['Low'], errors='coerce').to_numpy(),
            'close': pd.to_numeric(history['Close'], errors='coerce').to_numpy(),
            'adjusted_close': pd.to_numeric(
                history['Adj Close'], errors='coerce'
            ).to_numpy(),
            'volume': pd.to_numeric(history['Volume'], errors='coerce')
            .fillna(0)
            .astype('int64')
            .to_numpy(),
            'dividends': pd.to_numeric(
                history.get('Dividends', pd.Series(0.0, index=history.index)),
                errors='coerce',
            ).to_numpy(),
            'stock_splits': pd.to_numeric(
                history.get('Stock Splits', pd.Series(0.0, index=history.index)),
                errors='coerce',
            ).to_numpy(),
        }
    )
    price_columns = ['open', 'high', 'low', 'close', 'adjusted_close']
    normalized = normalized.dropna(subset=price_columns)
    normalized = normalized.drop_duplicates(subset=time_column, keep='last')
    return normalized.sort_values(time_column).reset_index(drop=True)


def normalize_requested_year(
    history: pd.DataFrame,
    year: int,
    interval: Interval,
) -> pd.DataFrame:
    """Select and normalize one requested year, rejecting empty results."""
    annual_history = history[history.index.year == year]
    if annual_history.empty:
        message = 'No price history was returned.'
        raise ValueError(message)
    normalized = normalize_history(annual_history, interval)
    if normalized.empty:
        message = 'No valid price rows were returned.'
        raise ValueError(message)
    return normalized


def write_year(
    history: pd.DataFrame,
    destination: Path,
    metadata: dict[bytes, bytes],
) -> None:
    """Atomically write one year of normalized history to Parquet."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(history, preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix='.data-',
        suffix='.parquet.tmp',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        pq.write_table(table, temporary_path, compression='zstd')
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def download_ticker(
    ticker: str,
    start_year: int,
    end_year: int,
    interval: Interval = Interval.DAILY,
) -> int:
    """Download and store the requested history for one ticker."""
    yf.config.debug.hide_exceptions = False
    stock = yf.Ticker(ticker)
    retrieved_at = datetime.now(tz=UTC).isoformat()
    files_written = 0
    failures: list[str] = []

    for year in range(start_year, end_year + 1):
        try:
            history = stock.history(
                start=f'{year}-01-01',
                end=f'{year + 1}-01-01',
                interval=interval.yfinance_value,
                auto_adjust=False,
                actions=True,
                repair=True,
            )
            normalized = normalize_requested_year(history, year, interval)
        except Exception as error:
            failures.append(f'{year}: {error}')
            continue

        exchange_timezone = str(history.index.tz or '')
        history_metadata = getattr(stock, 'history_metadata', {})
        currency = str(history_metadata.get('currency', ''))
        destination = (
            STOCKS_DIRECTORY
            / f'interval={interval.value}'
            / f'ticker={ticker}'
            / f'year={year}'
            / 'data.parquet'
        )
        metadata = {
            b'ticker': ticker.encode(),
            b'year': str(year).encode(),
            b'source': b'Yahoo Finance via yfinance',
            b'retrieved_at_utc': retrieved_at.encode(),
            b'exchange_timezone': exchange_timezone.encode(),
            b'currency': currency.encode(),
            b'bar_interval': interval.value.encode(),
        }
        write_year(normalized, destination, metadata)
        print(f'Wrote {len(normalized)} rows to {destination}')
        files_written += 1

    if failures:
        details = '; '.join(failures)
        raise RuntimeError(f'Failed years: {details}')
    return files_written


def download(
    tickers: set[str],
    years: tuple[int, int],
    interval: Interval = Interval.DAILY,
) -> int:
    """Download tickers concurrently while preserving successful results."""
    start_year, end_year = years
    failures = 0
    workers = min(6, len(tickers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                download_ticker,
                ticker,
                start_year,
                end_year,
                interval,
            ): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                future.result()
            except Exception as error:
                print(f'{ticker}: {error}', file=sys.stderr)
                failures += 1
    return 1 if failures else 0
