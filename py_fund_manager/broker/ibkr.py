"""Parse and validate Interactive Brokers monthly Activity Statement CSV files."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from py_fund_manager.schemas import Portfolio, Transaction


IBKR_BROKER_NAME = 'Interactive Brokers LLC'
IBKR_STATEMENT_TITLE = 'Activity Statement'
STATEMENT_SECTION = 'Statement'
ACCOUNT_SECTION = 'Account Information'
CASH_ACTIVITY_SECTION = 'Deposits & Withdrawals'
FIELD_HEADER = ('Field Name', 'Field Value')
CASH_ACTIVITY_HEADER = ('Currency', 'Settle Date', 'Description', 'Amount')
NON_TRANSACTIONAL_SECTIONS = frozenset(
    {
        STATEMENT_SECTION,
        ACCOUNT_SECTION,
        'Net Asset Value',
        'Change in NAV',
        'Mark-to-Market Performance Summary',
        'Cash Report',
        'Codes',
        'Notes/Legal Notes',
    }
)
PERIOD_PATTERN = re.compile(
    r'^(?P<start>[A-Z][a-z]+ \d{1,2}, \d{4}) - '
    r'(?P<end>[A-Z][a-z]+ \d{1,2}, \d{4})$'
)


@dataclass(frozen=True)
class IBKRCashActivity:
    """One date-only cash movement reported by an IBKR monthly statement."""

    currency: str
    settle_date: date
    description: str
    amount: Decimal
    source_line: int


@dataclass(frozen=True)
class IBKRActivityStatement:
    """Validated identity, period, and observed cash activity from one statement."""

    account_id: str
    base_currency: str
    period_start: date
    period_end: date
    generated_at: str
    cash_activity: tuple[IBKRCashActivity, ...]
    sections: tuple[str, ...]
    unconverted_sections: tuple[str, ...]


def load_activity_statement(
    path: Path,
    *,
    expected_account_id: str | None = None,
    expected_base_currency: str | None = None,
) -> IBKRActivityStatement:
    """Load an IBKR monthly statement without inventing ledger facts."""
    if not path.is_file():
        msg = f"IBKR statement '{path}' does not exist or is not a file"
        raise TypeError(msg)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = tuple(enumerate(csv.reader(stream), start=1))
    if not rows:
        msg = f'{path}: IBKR statement is empty'
        raise ValueError(msg)

    headers: dict[str, tuple[str, ...]] = {}
    data_rows: dict[str, list[tuple[int, tuple[str, ...]]]] = {}
    section_order: list[str] = []
    for line_number, row in rows:
        if len(row) < 2 or not row[0] or row[1] not in {'Header', 'Data'}:
            msg = f'{path}:{line_number}: invalid IBKR section row'
            raise ValueError(msg)
        section, row_kind = row[:2]
        fields = tuple(row[2:])
        if section not in data_rows:
            section_order.append(section)
            data_rows[section] = []
        if row_kind == 'Header':
            headers[section] = fields
        else:
            data_rows[section].append((line_number, fields))

    statement = _field_values(path, STATEMENT_SECTION, headers, data_rows)
    account = _field_values(path, ACCOUNT_SECTION, headers, data_rows)
    _require_value(path, statement, 'BrokerName', IBKR_BROKER_NAME)
    _require_value(path, statement, 'Title', IBKR_STATEMENT_TITLE)
    account_id = _required_field(path, account, 'Account')
    base_currency = _required_field(path, account, 'Base Currency').upper()
    _validate_expected(path, 'account', account_id, expected_account_id)
    _validate_expected(
        path,
        'base currency',
        base_currency,
        expected_base_currency.upper() if expected_base_currency else None,
    )
    period_start, period_end = _parse_period(
        path, _required_field(path, statement, 'Period')
    )
    generated_at = _required_field(path, statement, 'WhenGenerated')
    cash_activity = _cash_activity(path, headers, data_rows, period_start, period_end)
    unconverted_sections = tuple(
        section
        for section in section_order
        if section not in NON_TRANSACTIONAL_SECTIONS | {CASH_ACTIVITY_SECTION}
    )
    return IBKRActivityStatement(
        account_id=account_id,
        base_currency=base_currency,
        period_start=period_start,
        period_end=period_end,
        generated_at=generated_at,
        cash_activity=cash_activity,
        sections=tuple(section_order),
        unconverted_sections=unconverted_sections,
    )


def is_activity_statement(path: Path) -> bool:
    """Return whether a CSV starts with the supported IBKR statement marker."""
    if path.suffix.lower() != '.csv' or not path.is_file():
        return False
    with path.open(encoding='utf-8-sig', newline='') as stream:
        first_row = next(csv.reader(stream), None)
    return first_row == [STATEMENT_SECTION, 'Header', *FIELD_HEADER]


def require_importable_activity(statement: IBKRActivityStatement) -> None:
    """Reject statement rows that cannot satisfy the canonical ledger contract."""
    if statement.unconverted_sections:
        sections = ', '.join(statement.unconverted_sections)
        msg = f'IBKR statement contains unsupported activity sections: {sections}'
        raise ValueError(msg)
    if statement.cash_activity:
        first = statement.cash_activity[0]
        msg = (
            f'IBKR {CASH_ACTIVITY_SECTION} row {first.source_line} provides only a '
            'settlement date and no stable transaction ID; a timezone-aware event '
            'timestamp and broker identity are required before import'
        )
        raise ValueError(msg)


def read_activity_transactions(source: Path, portfolio: Portfolio) -> list[Transaction]:
    """Validate a native statement and return only fully identified ledger facts."""
    if portfolio.spec.broker.lower() != 'ibkr':
        msg = (
            f'{source}: IBKR statement requires portfolio broker '
            f"'ibkr'; found {portfolio.spec.broker!r}"
        )
        raise ValueError(msg)
    statement = load_activity_statement(
        source,
        expected_account_id=portfolio.spec.account_id,
        expected_base_currency=portfolio.spec.base_currency,
    )
    require_importable_activity(statement)
    return []


def _field_values(
    path: Path,
    section: str,
    headers: dict[str, tuple[str, ...]],
    data_rows: dict[str, list[tuple[int, tuple[str, ...]]]],
) -> dict[str, str]:
    """Read a required two-column metadata section with duplicate detection."""
    if headers.get(section) != FIELD_HEADER:
        msg = f'{path}: {section} must use header {FIELD_HEADER!r}'
        raise ValueError(msg)
    values: dict[str, str] = {}
    for line_number, fields in data_rows.get(section, []):
        if len(fields) != 2 or not fields[0]:
            msg = f'{path}:{line_number}: invalid {section} field row'
            raise ValueError(msg)
        name, value = fields
        if name in values:
            msg = f'{path}:{line_number}: duplicate {section} field {name!r}'
            raise ValueError(msg)
        values[name] = value
    return values


def _required_field(path: Path, fields: dict[str, str], name: str) -> str:
    """Return one nonempty required statement field."""
    value = fields.get(name, '').strip()
    if not value:
        msg = f'{path}: missing required IBKR field {name!r}'
        raise ValueError(msg)
    return value


def _require_value(
    path: Path, fields: dict[str, str], name: str, expected: str
) -> None:
    """Require a statement discriminator to match the supported export."""
    actual = _required_field(path, fields, name)
    if actual != expected:
        msg = f'{path}: unsupported {name} {actual!r}; expected {expected!r}'
        raise ValueError(msg)


def _validate_expected(
    path: Path, field: str, actual: str, expected: str | None
) -> None:
    """Require statement identity to match the selected portfolio when supplied."""
    if expected is not None and actual != expected:
        msg = f'{path}: IBKR statement {field} {actual!r}; expected {expected!r}'
        raise ValueError(msg)


def _parse_period(path: Path, value: str) -> tuple[date, date]:
    """Parse the inclusive English-language monthly statement period."""
    match = PERIOD_PATTERN.fullmatch(value)
    if match is None:
        msg = f'{path}: invalid IBKR statement period {value!r}'
        raise ValueError(msg)
    try:
        start = date.strptime(match['start'], '%B %d, %Y')
        end = date.strptime(match['end'], '%B %d, %Y')
    except ValueError as error:
        raise ValueError(f'{path}: invalid IBKR statement period {value!r}') from error
    if end < start:
        msg = f'{path}: IBKR statement period ends before it starts'
        raise ValueError(msg)
    return start, end


def _cash_activity(
    path: Path,
    headers: dict[str, tuple[str, ...]],
    data_rows: dict[str, list[tuple[int, tuple[str, ...]]]],
    period_start: date,
    period_end: date,
) -> tuple[IBKRCashActivity, ...]:
    """Parse non-total cash rows while retaining their source locations."""
    rows = data_rows.get(CASH_ACTIVITY_SECTION)
    if rows is None:
        return ()
    if headers.get(CASH_ACTIVITY_SECTION) != CASH_ACTIVITY_HEADER:
        msg = (
            f'{path}: {CASH_ACTIVITY_SECTION} must use header {CASH_ACTIVITY_HEADER!r}'
        )
        raise ValueError(msg)
    activity: list[IBKRCashActivity] = []
    for line_number, fields in rows:
        if len(fields) != 4:
            msg = f'{path}:{line_number}: invalid {CASH_ACTIVITY_SECTION} row'
            raise ValueError(msg)
        currency, settle_date_text, description, amount_text = fields
        if currency == 'Total':
            continue
        currency = currency.strip().upper()
        description = description.strip()
        if len(currency) != 3 or not description:
            msg = f'{path}:{line_number}: invalid {CASH_ACTIVITY_SECTION} identity'
            raise ValueError(msg)
        try:
            settle_date = date.fromisoformat(settle_date_text)
            amount = Decimal(amount_text)
        except (InvalidOperation, ValueError) as error:
            raise ValueError(
                f'{path}:{line_number}: invalid {CASH_ACTIVITY_SECTION} value'
            ) from error
        if not amount.is_finite() or amount == 0:
            msg = (
                f'{path}:{line_number}: cash activity amount must be finite and nonzero'
            )
            raise ValueError(msg)
        if not period_start <= settle_date <= period_end:
            msg = (
                f'{path}:{line_number}: cash activity date is outside statement period'
            )
            raise ValueError(msg)
        activity.append(
            IBKRCashActivity(
                currency=currency,
                settle_date=settle_date,
                description=description,
                amount=amount,
                source_line=line_number,
            )
        )
    return tuple(activity)
