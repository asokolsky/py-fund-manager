"""Tests for parsing Interactive Brokers monthly Activity Statement exports."""

import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from py_fund_manager.broker.ibkr import (
    is_activity_statement,
    load_activity_statement,
    require_importable_activity,
)
from py_fund_manager.broker.imports import import_broker_activity
from py_fund_manager.portfolio import (
    create_portfolio,
    initialize_opening_balances,
)


class TestIBKRActivityStatement(unittest.TestCase):
    """Verify strict parsing without inventing canonical transaction facts."""

    def test_parses_bom_cash_activity_and_optional_sections(self) -> None:
        """Parse observed metadata and ignore a summary total row."""
        with tempfile.TemporaryDirectory() as directory:
            statement_path = Path(directory) / 'statement.csv'
            self._write_statement(
                statement_path,
                '\ufeff',
                (
                    'Deposits & Withdrawals,Header,Currency,Settle Date,'
                    'Description,Amount\n'
                    'Deposits & Withdrawals,Data,USD,2026-07-14,'
                    'Electronic Fund Transfer,100\n'
                    'Deposits & Withdrawals,Data,Total,,,100\n'
                ),
            )

            statement = load_activity_statement(
                statement_path,
                expected_account_id='U1234567',
                expected_base_currency='usd',
            )

        self.assertEqual(statement.account_id, 'U1234567')
        self.assertEqual(statement.base_currency, 'USD')
        self.assertEqual(statement.period_start, date(2026, 7, 1))
        self.assertEqual(statement.period_end, date(2026, 7, 31))
        self.assertEqual(len(statement.cash_activity), 1)
        self.assertEqual(statement.cash_activity[0].amount, Decimal(100))
        self.assertEqual(statement.cash_activity[0].settle_date, date(2026, 7, 14))

    def test_accepts_month_without_cash_activity_section(self) -> None:
        """Treat an omitted optional section as no observed cash activity."""
        with tempfile.TemporaryDirectory() as directory:
            statement_path = Path(directory) / 'statement.csv'
            self._write_statement(statement_path)

            statement = load_activity_statement(statement_path)

        self.assertEqual(statement.cash_activity, ())
        require_importable_activity(statement)

    def test_import_archives_validated_no_activity_statement(self) -> None:
        """Preserve an account-matched statement that contains no ledger events."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_directory = create_portfolio(
                root, 'brokerage', broker='ibkr', account_id='U1234567'
            )
            initialize_opening_balances(
                portfolio_directory,
                {'USD': Decimal(100)},
                occurred_at=datetime(2026, 6, 30, tzinfo=UTC),
            )
            statement_path = root / 'statement.csv'
            self._write_statement(statement_path)

            result = import_broker_activity(portfolio_directory, statement_path)

        self.assertEqual(result.imported, 0)
        self.assertEqual(result.skipped, 0)

    def test_import_rejects_unidentifiable_cash_without_writes(self) -> None:
        """Reject date-only cash rows before preserving or changing portfolio data."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_directory = create_portfolio(
                root, 'brokerage', broker='ibkr', account_id='U1234567'
            )
            initialize_opening_balances(
                portfolio_directory,
                {'USD': Decimal(100)},
                occurred_at=datetime(2026, 6, 30, tzinfo=UTC),
            )
            ledger = portfolio_directory / 'transactions.csv'
            original_ledger = ledger.read_bytes()
            statement_path = root / 'statement.csv'
            self._write_statement(
                statement_path,
                activity=(
                    'Deposits & Withdrawals,Header,Currency,Settle Date,'
                    'Description,Amount\n'
                    'Deposits & Withdrawals,Data,USD,2026-07-14,Deposit,100\n'
                ),
            )

            with self.assertRaisesRegex(ValueError, 'no stable transaction ID'):
                import_broker_activity(portfolio_directory, statement_path)

            self.assertEqual(ledger.read_bytes(), original_ledger)
            self.assertFalse((portfolio_directory / 'imports').exists())

    def test_statement_detection_is_specific(self) -> None:
        """Distinguish IBKR statements from canonical activity CSV files."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            statement_path = root / 'statement.csv'
            canonical_path = root / 'activity.csv'
            self._write_statement(statement_path)
            canonical_path.write_text(
                'occurred_at,event,asset,external_id\n', encoding='utf-8'
            )

            self.assertTrue(is_activity_statement(statement_path))
            self.assertFalse(is_activity_statement(canonical_path))

    def test_eager_broker_exports_do_not_cycle_with_portfolio(self) -> None:
        """Import persistence before conventional broker package re-exports."""
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import py_fund_manager.portfolio; '
                    'from py_fund_manager.broker import '
                    'HistoricalBroker, execute_rebalance_plan'
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_unconverted_statement_sections(self) -> None:
        """Fail closed when a statement contains activity without a defined mapping."""
        with tempfile.TemporaryDirectory() as directory:
            statement_path = Path(directory) / 'statement.csv'
            self._write_statement(
                statement_path,
                activity=(
                    'Trades,Header,DataDiscriminator,Asset Category,Currency,'
                    'Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee,TradeID\n'
                    'Trades,Data,Order,Stocks,USD,EXAMPLE,"2026-07-14, '
                    '10:00:00",1,10,-10,-1,123\n'
                ),
            )
            statement = load_activity_statement(statement_path)

        with self.assertRaisesRegex(
            ValueError, 'unsupported activity sections: Trades'
        ):
            require_importable_activity(statement)

    def test_rejects_account_and_currency_mismatches(self) -> None:
        """Require the private statement to belong to the selected portfolio."""
        with tempfile.TemporaryDirectory() as directory:
            statement_path = Path(directory) / 'statement.csv'
            self._write_statement(statement_path)

            for expected_account, expected_currency, message in (
                ('U7654321', 'USD', 'account'),
                ('U1234567', 'EUR', 'base currency'),
            ):
                with (
                    self.subTest(message=message),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    load_activity_statement(
                        statement_path,
                        expected_account_id=expected_account,
                        expected_base_currency=expected_currency,
                    )

    def test_rejects_malformed_cash_section_with_source_line(self) -> None:
        """Identify a malformed section-local data row without partial output."""
        with tempfile.TemporaryDirectory() as directory:
            statement_path = Path(directory) / 'statement.csv'
            self._write_statement(
                statement_path,
                activity=(
                    'Deposits & Withdrawals,Header,Currency,Settle Date,'
                    'Description,Amount\n'
                    'Deposits & Withdrawals,Data,USD,not-a-date,Deposit,100\n'
                ),
            )

            with self.assertRaisesRegex(ValueError, r'statement\.csv:14:'):
                load_activity_statement(statement_path)

    def test_refuses_to_invent_timestamp_or_identity(self) -> None:
        """Keep date-only unidentified cash rows out of the canonical ledger."""
        with tempfile.TemporaryDirectory() as directory:
            statement_path = Path(directory) / 'statement.csv'
            self._write_statement(
                statement_path,
                activity=(
                    'Deposits & Withdrawals,Header,Currency,Settle Date,'
                    'Description,Amount\n'
                    'Deposits & Withdrawals,Data,USD,2026-07-14,Deposit,100\n'
                ),
            )
            statement = load_activity_statement(statement_path)

        with self.assertRaisesRegex(
            ValueError, 'timezone-aware event timestamp and broker identity'
        ):
            require_importable_activity(statement)

    @staticmethod
    def _write_statement(
        path: Path,
        prefix: str = '',
        activity: str = '',
    ) -> None:
        """Write a minimal sanitized statement matching the observed structure."""
        path.write_text(
            prefix + 'Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers LLC\n'
            'Statement,Data,Title,Activity Statement\n'
            'Statement,Data,Period,"July 1, 2026 - July 31, 2026"\n'
            'Statement,Data,WhenGenerated,"2026-09-02, 05:19:33 EDT"\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U1234567\n'
            'Account Information,Data,Base Currency,USD\n'
            'Net Asset Value,Header,Asset Class,Prior Total,Current Long,'
            'Current Short,Current Total,Change\n'
            'Net Asset Value,Data,Cash ,0,100,0,100,100\n'
            'Net Asset Value,Header,Time Weighted Rate of Return\n'
            'Net Asset Value,Data,0%\n' + activity,
            encoding='utf-8',
        )
