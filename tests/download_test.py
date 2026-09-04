"""Tests for historical-price downloading and storage."""

import tempfile
import threading
import unittest
from argparse import ArgumentTypeError
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyarrow.parquet as pq

from py_fund_manager import download as downloader


class FakeTicker:
    """Provide deterministic yfinance history data for downloader tests."""

    def __init__(self, history: pd.DataFrame) -> None:
        """Initialize the fake with history returned by subsequent requests."""
        self._history = history
        self.history_metadata = {'currency': 'USD', 'exchangeName': 'NMS'}
        self.history_kwargs: dict[str, object] = {}
        self.history_calls: list[dict[str, object]] = []

    def history(self, **kwargs: object) -> pd.DataFrame:
        """Capture request arguments and return the configured history."""
        self.history_kwargs = kwargs
        self.history_calls.append(kwargs)
        return self._history


class TestDownload(unittest.TestCase):
    """Verify download parsing, normalization, and Parquet output."""

    def test_parse_tickers(self) -> None:
        """Normalize, deduplicate, and validate comma-separated tickers."""
        self.assertEqual(
            downloader.tickers_argument('aapl, MSFT,aapl'), {'AAPL', 'MSFT'}
        )
        with self.assertRaises(ArgumentTypeError):
            downloader.tickers_argument('AAPL,../../tmp')

    def test_load_tickers_from_file(self) -> None:
        """Load, normalize, and deduplicate ticker-file entries."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            ticker_file = Path(temporary_directory) / 'tickers.txt'
            ticker_file.write_text(
                '# Watchlist\n\naapl\nMSFT\nAAPL\n', encoding='utf-8'
            )

            self.assertEqual(
                downloader.tickers_argument(f'@{ticker_file}'), {'AAPL', 'MSFT'}
            )

    def test_ticker_file_error_includes_line_number(self) -> None:
        """Identify the source line containing a malformed ticker."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            ticker_file = Path(temporary_directory) / 'tickers.txt'
            ticker_file.write_text('AAPL\nnot/a/ticker\n', encoding='utf-8')

            with self.assertRaisesRegex(ArgumentTypeError, 'line 2'):
                downloader.tickers_argument(f'@{ticker_file}')

    def test_reject_invalid_ticker_file_sources(self) -> None:
        """Reject absent, empty, directory, and invalid UTF-8 ticker sources."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            empty_file = directory / 'empty.txt'
            empty_file.write_text('# No symbols\n', encoding='utf-8')
            invalid_file = directory / 'invalid.txt'
            invalid_file.write_bytes(b'\xff')

            invalid_sources = (
                '@',
                f'@{directory / "missing.txt"}',
                f'@{directory}',
                f'@{empty_file}',
                f'@{invalid_file}',
            )
            for source in invalid_sources:
                with self.subTest(source=source), self.assertRaises(ArgumentTypeError):
                    downloader.tickers_argument(source)

    def test_parse_year_range(self) -> None:
        """Accept single years and ordered inclusive year ranges."""
        self.assertEqual(downloader.inclusive_year_range('2024'), (2024, 2024))
        self.assertEqual(downloader.inclusive_year_range('2024-2025'), (2024, 2025))
        with self.assertRaises(ArgumentTypeError):
            downloader.inclusive_year_range('2025-2024')

    def test_hourly_history_preserves_each_timestamp(self) -> None:
        """Retain every intraday bar by preserving complete timestamps."""
        history = pd.DataFrame(
            {
                'Open': [100.0, 101.0],
                'High': [102.0, 103.0],
                'Low': [99.0, 100.0],
                'Close': [101.0, 102.0],
                'Adj Close': [101.0, 102.0],
                'Volume': [1_000, 2_000],
            },
            index=pd.DatetimeIndex(
                ['2026-08-07 09:30', '2026-08-07 10:30'],
                tz='America/New_York',
            ),
        )

        normalized = downloader.normalize_history(history, downloader.Interval.HOURLY)

        self.assertEqual(len(normalized), 2)
        self.assertIn('timestamp', normalized.columns)

    def test_download_writes_one_parquet_file_per_year(self) -> None:
        """Partition downloaded history by interval, ticker, and year."""
        history = pd.DataFrame(
            {
                'Open': [100.0, 110.0],
                'High': [102.0, 112.0],
                'Low': [99.0, 109.0],
                'Close': [101.0, 111.0],
                'Adj Close': [100.5, 110.5],
                'Volume': [1_000, 2_000],
                'Dividends': [0.0, 0.25],
                'Stock Splits': [0.0, 0.0],
            },
            index=pd.DatetimeIndex(
                ['2024-06-03 16:00', '2025-06-02 16:00'],
                tz='America/New_York',
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            fake_ticker = FakeTicker(history)
            with (
                patch.object(downloader.yf, 'Ticker', return_value=fake_ticker),
                patch.object(downloader, 'STOCKS_DIRECTORY', output_directory),
            ):
                files_written = downloader.download_ticker(
                    'AAPL', 2024, 2025, downloader.Interval.WEEKLY
                )

            self.assertEqual(files_written, 2)
            self.assertEqual(fake_ticker.history_kwargs['interval'], '1wk')
            self.assertEqual(
                [call['start'] for call in fake_ticker.history_calls],
                ['2024-01-01', '2025-01-01'],
            )
            self.assertNotIn('raise_errors', fake_ticker.history_kwargs)
            self.assertFalse(downloader.yf.config.debug.hide_exceptions)
            for year in (2024, 2025):
                parquet_path = (
                    output_directory
                    / 'interval=1w'
                    / 'ticker=AAPL'
                    / f'year={year}'
                    / 'data.parquet'
                )
                table = pq.read_table(parquet_path)
                self.assertEqual(table.num_rows, 1)
                self.assertEqual(table.schema.metadata[b'ticker'], b'AAPL')
                self.assertEqual(table.schema.metadata[b'bar_interval'], b'1w')
                self.assertEqual(table.schema.metadata[b'exchange_calendar'], b'XNAS')

    def test_later_year_is_written_after_earlier_year_fails(self) -> None:
        """Continue requesting later years and preserve their successful files."""
        history = pd.DataFrame(
            {
                'Open': [110.0],
                'High': [112.0],
                'Low': [109.0],
                'Close': [111.0],
                'Adj Close': [110.5],
                'Volume': [2_000],
            },
            index=pd.DatetimeIndex(['2025-06-02 16:00'], tz='America/New_York'),
        )
        fake_ticker = FakeTicker(history)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            with (
                patch.object(
                    fake_ticker,
                    'history',
                    side_effect=[ValueError('outside retention window'), history],
                ) as history_mock,
                patch.object(downloader.yf, 'Ticker', return_value=fake_ticker),
                patch.object(downloader, 'STOCKS_DIRECTORY', output_directory),
                self.assertRaisesRegex(RuntimeError, '2024: outside retention window'),
            ):
                downloader.download_ticker(
                    'AAPL', 2024, 2025, downloader.Interval.HOURLY
                )

            self.assertEqual(history_mock.call_count, 2)
            self.assertTrue(
                (
                    output_directory
                    / 'interval=1h'
                    / 'ticker=AAPL'
                    / 'year=2025'
                    / 'data.parquet'
                ).is_file()
            )

    def test_multiple_tickers_download_concurrently(self) -> None:
        """Start independent ticker downloads in parallel workers."""
        barrier = threading.Barrier(2)
        downloaded: set[str] = set()
        lock = threading.Lock()

        def fake_download_ticker(
            ticker: str,
            _start_year: int,
            _end_year: int,
            _interval: downloader.Interval,
            *,
            stocks_directory: Path,
            progress_stream: StringIO,
        ) -> int:
            self.assertIsInstance(stocks_directory, Path)
            barrier.wait(timeout=1)
            with lock:
                downloaded.add(ticker)
            return 1

        with patch.object(
            downloader, 'download_ticker', side_effect=fake_download_ticker
        ):
            result = downloader.download(
                {'AAPL', 'MSFT'}, (2025, 2025), downloader.Interval.DAILY
            )

        self.assertEqual(result, 0)
        self.assertEqual(downloaded, {'AAPL', 'MSFT'})

    def test_ticker_failure_sets_error_status_without_stopping_others(self) -> None:
        """Report one failed ticker after allowing another ticker to succeed."""
        downloaded: set[str] = set()

        def fake_download_ticker(
            ticker: str,
            _start_year: int,
            _end_year: int,
            _interval: downloader.Interval,
            *,
            stocks_directory: Path,
            progress_stream: StringIO,
        ) -> int:
            self.assertIsInstance(stocks_directory, Path)
            if ticker == 'FAIL':
                message = 'download failed'
                raise RuntimeError(message)
            downloaded.add(ticker)
            return 1

        errors = StringIO()
        with (
            patch.object(
                downloader, 'download_ticker', side_effect=fake_download_ticker
            ),
            redirect_stderr(errors),
        ):
            result = downloader.download({'AAPL', 'FAIL'}, (2025, 2025))

        self.assertEqual(result, 1)
        self.assertEqual(downloaded, {'AAPL'})
        self.assertIn('FAIL: download failed', errors.getvalue())

    def test_normalization_sorts_deduplicates_and_supplies_actions(self) -> None:
        """Normalize ordering, duplicates, missing actions, and invalid prices."""
        history = pd.DataFrame(
            {
                'Open': [102.0, 100.0, 103.0, float('nan')],
                'High': [103.0, 101.0, 104.0, 105.0],
                'Low': [101.0, 99.0, 102.0, 103.0],
                'Close': [102.5, 100.5, 103.5, 104.5],
                'Adj Close': [102.5, 100.5, 103.5, 104.5],
                'Volume': [200, 100, 300, 400],
            },
            index=pd.DatetimeIndex(
                ['2025-01-02', '2025-01-01', '2025-01-02', '2025-01-03'],
                tz='America/New_York',
            ),
        )

        normalized = downloader.normalize_history(history)

        self.assertEqual(
            normalized['date'].tolist(), history.index.date[:2].tolist()[::-1]
        )
        self.assertEqual(normalized['close'].tolist(), [100.5, 103.5])
        self.assertEqual(normalized['dividends'].tolist(), [0.0, 0.0])
        self.assertEqual(normalized['stock_splits'].tolist(), [0.0, 0.0])

    def test_normalization_rejects_missing_required_columns(self) -> None:
        """Reject market frames that cannot satisfy the Parquet schema."""
        with self.assertRaisesRegex(ValueError, 'Adj Close'):
            downloader.normalize_history(pd.DataFrame({'Open': [1.0]}))

    def test_atomic_write_preserves_existing_file_after_failure(self) -> None:
        """Keep the previous Parquet file and remove temporary output on failure."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / 'data.parquet'
            destination.write_bytes(b'previous data')
            history = pd.DataFrame({'date': [pd.Timestamp('2025-01-01').date()]})

            with (
                patch.object(
                    downloader.pq, 'write_table', side_effect=OSError('disk full')
                ),
                self.assertRaisesRegex(OSError, 'disk full'),
            ):
                downloader.write_year(history, destination, {})

            self.assertEqual(destination.read_bytes(), b'previous data')
            self.assertEqual(list(destination.parent.glob('.data-*')), [])

    def test_invalid_exchange_metadata_preserves_existing_partition(self) -> None:
        """Reject unresolved exchange calendars before replacing cached data."""
        history = pd.DataFrame(
            {
                'Open': [100.0],
                'High': [102.0],
                'Low': [99.0],
                'Close': [101.0],
                'Adj Close': [100.5],
                'Volume': [1_000],
            },
            index=pd.DatetimeIndex(['2026-06-03'], tz='America/New_York'),
        )
        for exchange_name in ('', 'NOT-A-REAL-EXCHANGE'):
            with (
                self.subTest(exchange_name=exchange_name),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                output_directory = Path(temporary_directory)
                destination = (
                    output_directory / 'interval=1d/ticker=AAPL/year=2026/data.parquet'
                )
                destination.parent.mkdir(parents=True)
                destination.write_bytes(b'previous data')
                fake_ticker = FakeTicker(history)
                fake_ticker.history_metadata['exchangeName'] = exchange_name

                with (
                    patch.object(downloader.yf, 'Ticker', return_value=fake_ticker),
                    self.assertRaisesRegex(RuntimeError, 'exchange identifier'),
                ):
                    downloader.download_ticker(
                        'AAPL',
                        2026,
                        2026,
                        stocks_directory=output_directory,
                    )

                self.assertEqual(destination.read_bytes(), b'previous data')

    def test_non_us_exchange_alias_is_canonicalized_before_replace(self) -> None:
        """Resolve a supported Yahoo exchange alias before replacing its cache."""
        history = pd.DataFrame(
            {
                'Open': [100.0],
                'High': [102.0],
                'Low': [99.0],
                'Close': [101.0],
                'Adj Close': [100.5],
                'Volume': [1_000],
            },
            index=pd.DatetimeIndex(['2026-06-03'], tz='Europe/London'),
        )
        fake_ticker = FakeTicker(history)
        fake_ticker.history_metadata['exchangeName'] = 'LSE'
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            destination = (
                output_directory / 'interval=1d/ticker=VOD.L/year=2026/data.parquet'
            )
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b'previous data')

            with patch.object(downloader.yf, 'Ticker', return_value=fake_ticker):
                downloader.download_ticker(
                    'VOD.L', 2026, 2026, stocks_directory=output_directory
                )

            metadata = pq.read_metadata(destination).metadata or {}
            self.assertEqual(metadata[b'exchange_calendar'], b'XLON')

    def test_invalid_timezone_and_currency_are_rejected_before_write(self) -> None:
        """Require usable timezone and currency metadata for every partition."""
        history = pd.DataFrame(
            {
                'Open': [100.0],
                'High': [102.0],
                'Low': [99.0],
                'Close': [101.0],
                'Adj Close': [100.5],
                'Volume': [1_000],
            },
            index=pd.DatetimeIndex(['2026-06-03'], tz='America/New_York'),
        )
        cases: tuple[tuple[str, pd.DataFrame, dict[str, str], str], ...] = (
            ('currency', history, {'currency': ''}, 'trading currency'),
            (
                'timezone',
                history.tz_localize(None),
                {},
                'exchange timezone',
            ),
        )
        for name, case_history, metadata, message in cases:
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                fake_ticker = FakeTicker(case_history)
                fake_ticker.history_metadata.update(metadata)
                output_directory = Path(temporary_directory)
                with (
                    patch.object(downloader.yf, 'Ticker', return_value=fake_ticker),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    downloader.download_ticker(
                        'AAPL', 2026, 2026, stocks_directory=output_directory
                    )

                self.assertFalse(list(output_directory.rglob('data.parquet')))


if __name__ == '__main__':
    unittest.main()
