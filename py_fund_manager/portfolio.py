"""Portfolio, transaction, and investment-strategy data models."""

from __future__ import annotations

import csv
import os
import re
import shutil
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
WEIGHT_TOLERANCE = Decimal('0.000001')
PORTFOLIO_ID_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
TICKER_PATTERN = re.compile(r'^[A-Z0-9][A-Z0-9.^=-]*$')
TRANSACTION_FIELDS = (
    'id',
    'occurred_at',
    'type',
    'ticker',
    'quantity',
    'price',
    'amount',
    'cost_basis',
    'currency',
    'fees',
    'external_id',
)


class TransactionType(StrEnum):
    """Kinds of facts accepted by the portfolio transaction ledger."""

    OPENING_POSITION = 'opening_position'
    OPENING_CASH = 'opening_cash'
    POSITION_ADJUSTMENT = 'position_adjustment'
    BUY = 'buy'
    SELL = 'sell'
    DIVIDEND = 'dividend'
    INTEREST = 'interest'
    FEE = 'fee'
    DEPOSIT = 'deposit'
    WITHDRAWAL = 'withdrawal'
    SPLIT = 'split'
    TRANSFER_IN = 'transfer_in'
    TRANSFER_OUT = 'transfer_out'


class Portfolio(BaseModel):
    """Identity and configuration for one investment account."""

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        str_strip_whitespace=True,
        validate_by_name=True,
    )

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, pattern=PORTFOLIO_ID_PATTERN)
    name: str = Field(min_length=1)
    broker: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    base_currency: str = Field(min_length=3, max_length=3)
    strategy_id: str | None = Field(default=None, alias='strategy')
    opened_on: date | None = None

    @field_validator('base_currency', mode='before')
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        """Normalize a serialized ISO currency code."""
        return value.strip().upper() if isinstance(value, str) else value


class Transaction(BaseModel):
    """One immutable fact imported from an investment account."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    occurred_at: datetime
    type: TransactionType
    currency: str = Field(min_length=3, max_length=3)
    ticker: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, ge=0)
    cost_basis: Decimal | None = Field(default=None, ge=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)
    external_id: str | None = None

    @field_validator('ticker', 'currency', mode='before')
    @classmethod
    def normalize_code(cls, value: object) -> object:
        """Normalize ticker and currency codes before constraints run."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator('ticker')
    @classmethod
    def validate_ticker(cls, value: str | None) -> str | None:
        """Reject ticker values outside the supported Yahoo-style alphabet."""
        if value is not None and not TICKER_PATTERN.fullmatch(value):
            msg = 'ticker has an invalid format'
            raise ValueError(msg)
        return value

    @field_validator('occurred_at')
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        """Require an unambiguous transaction timestamp."""
        if value.tzinfo is None:
            msg = 'occurred_at must include a UTC offset'
            raise ValueError(msg)
        return value

    @model_validator(mode='after')
    def validate_security_event(self) -> Self:
        """Require security identity and quantity for position-changing facts."""
        security_events = {
            TransactionType.OPENING_POSITION,
            TransactionType.POSITION_ADJUSTMENT,
            TransactionType.BUY,
            TransactionType.SELL,
            TransactionType.SPLIT,
            TransactionType.TRANSFER_IN,
            TransactionType.TRANSFER_OUT,
        }
        if self.type in security_events and self.ticker is None:
            msg = f'{self.type} requires ticker'
            raise ValueError(msg)
        if self.type in security_events and self.quantity is None:
            msg = f'{self.type} requires quantity'
            raise ValueError(msg)
        if self.type != TransactionType.POSITION_ADJUSTMENT and (
            self.quantity is not None and self.quantity <= 0
        ):
            msg = f'{self.type} quantity must be positive'
            raise ValueError(msg)
        return self


class TargetAllocation(BaseModel):
    """Validated target weights for one investment strategy."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    type: Literal['target_weights']
    positions: dict[str, Decimal] = Field(min_length=1)

    @field_validator('positions', mode='before')
    @classmethod
    def normalize_tickers(cls, value: object) -> object:
        """Normalize strategy ticker keys before validating their weights."""
        if not isinstance(value, dict):
            return value
        return {
            ticker.strip().upper() if isinstance(ticker, str) else ticker: weight
            for ticker, weight in value.items()
        }

    @field_validator('positions')
    @classmethod
    def validate_weights(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        """Require valid tickers, nonnegative weights, and a complete allocation."""
        if any(not TICKER_PATTERN.fullmatch(ticker) for ticker in value):
            msg = 'strategy contains an invalid ticker'
            raise ValueError(msg)
        if any(weight < 0 for weight in value.values()):
            msg = 'strategy weights must be nonnegative'
            raise ValueError(msg)
        if abs(sum(value.values()) - Decimal(1)) > WEIGHT_TOLERANCE:
            msg = 'strategy weights must total 1.0'
            raise ValueError(msg)
        return value


class Strategy(BaseModel):
    """A target allocation that can be applied to a portfolio."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    allocation: TargetAllocation
    benchmark: str | None = None

    @property
    def target_weights(self) -> dict[str, Decimal]:
        """Expose target weights directly for allocation calculations."""
        return self.allocation.positions


def create_portfolio(data_directory: Path, portfolio_id: str) -> Path:
    """Create a portfolio directory and its initial YAML configuration."""
    if not PORTFOLIO_ID_PATTERN.fullmatch(portfolio_id):
        msg = 'portfolio ID must use lowercase kebab-case'
        raise ValueError(msg)
    portfolio_directory = data_directory / 'portfolios' / portfolio_id
    portfolio_directory.mkdir(parents=True, exist_ok=False)
    broker = portfolio_id.partition('-')[0]
    portfolio = Portfolio(
        id=portfolio_id,
        name=portfolio_id,
        broker=broker,
        account_id=portfolio_id,
        base_currency='USD',
    )
    document = portfolio.model_dump(mode='json', by_alias=True, exclude_none=True)
    _atomic_write_text(
        portfolio_directory / 'portfolio.yaml',
        yaml.safe_dump(document, sort_keys=False),
    )
    return portfolio_directory


def import_opening_positions(
    portfolio_directory: Path,
    source: Path,
    *,
    occurred_at: datetime | None = None,
) -> int:
    """Import canonical broker holdings as an opening transaction ledger."""
    if not source.is_file():
        msg = f"holdings file '{source}' does not exist or is not a file"
        raise ValueError(msg)
    ledger_path = portfolio_directory / 'transactions.csv'
    if ledger_path.exists():
        msg = f'{ledger_path} already exists; opening positions cannot be replaced'
        raise FileExistsError(msg)

    import_time = occurred_at or datetime.now(UTC)
    if import_time.tzinfo is None:
        msg = 'opening-position time must include a UTC offset'
        raise ValueError(msg)
    rows = _read_opening_positions(source, import_time)

    imports_directory = portfolio_directory / 'imports'
    imports_directory.mkdir(exist_ok=True)
    preserved_source = imports_directory / source.name
    if preserved_source.exists():
        msg = f'{preserved_source} already exists; source import was not replaced'
        raise FileExistsError(msg)
    _atomic_copy(source, preserved_source)
    try:
        _atomic_write_csv(ledger_path, rows)
    except Exception:
        preserved_source.unlink()
        raise
    return len(rows)


def load_portfolio(path: Path) -> Portfolio:
    """Load and validate portfolio configuration from a YAML file."""
    return Portfolio.model_validate(_load_yaml_mapping(path))


def load_strategy(path: Path) -> Strategy:
    """Load and validate target allocation from a YAML file."""
    return Strategy.model_validate(_load_yaml_mapping(path))


def load_transactions(path: Path) -> list[Transaction]:
    """Load and validate an ordered transaction ledger from CSV."""
    transactions: list[Transaction] = []
    seen_ids: set[str] = set()
    with path.open(newline='', encoding='utf-8') as ledger_file:
        for line_number, row in enumerate(csv.DictReader(ledger_file), start=2):
            context = Path(f'{path}:{line_number}')
            normalized = {key: value for key, value in row.items() if value != ''}
            transaction = Transaction.model_validate(normalized)
            if transaction.id in seen_ids:
                msg = f'{context}: duplicate transaction id {transaction.id}'
                raise ValueError(msg)
            seen_ids.add(transaction.id)
            transactions.append(transaction)
    return transactions


def _read_opening_positions(
    source: Path, occurred_at: datetime
) -> list[dict[str, str]]:
    """Validate canonical holdings CSV rows and map them to ledger rows."""
    rows: list[dict[str, str]] = []
    seen_tickers: set[str] = set()
    with source.open(newline='', encoding='utf-8-sig') as source_file:
        reader = csv.DictReader(source_file)
        required_fields = {'ticker', 'quantity'}
        if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
            msg = f'{source}: required CSV columns are ticker and quantity'
            raise ValueError(msg)
        for line_number, source_row in enumerate(reader, start=2):
            context = Path(f'{source}:{line_number}')
            transaction = Transaction.model_validate(
                {
                    'id': f'opening-{line_number - 1:06d}',
                    'occurred_at': occurred_at,
                    'type': TransactionType.OPENING_POSITION,
                    'ticker': source_row.get('ticker'),
                    'quantity': source_row.get('quantity'),
                    'cost_basis': source_row.get('cost_basis') or None,
                    'currency': source_row.get('currency') or 'USD',
                    'external_id': source_row.get('external_id')
                    or f'{source.name}:{line_number}',
                }
            )
            if transaction.ticker in seen_tickers:
                msg = f'{context}: duplicate opening position for {transaction.ticker}'
                raise ValueError(msg)
            if transaction.ticker is None:
                msg = f'{context}: opening position requires ticker'
                raise ValueError(msg)
            seen_tickers.add(transaction.ticker)
            rows.append(
                {
                    'id': transaction.id,
                    'occurred_at': transaction.occurred_at.isoformat(),
                    'type': transaction.type,
                    'ticker': transaction.ticker,
                    'quantity': str(transaction.quantity),
                    'price': '',
                    'amount': '',
                    'cost_basis': (
                        ''
                        if transaction.cost_basis is None
                        else str(transaction.cost_basis)
                    ),
                    'currency': transaction.currency,
                    'fees': '',
                    'external_id': transaction.external_id or '',
                }
            )
    if not rows:
        msg = f'{source}: holdings CSV contains no positions'
        raise ValueError(msg)
    return rows


def _atomic_write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write ledger rows atomically without replacing an existing ledger."""
    with tempfile.NamedTemporaryFile(
        mode='w',
        newline='',
        encoding='utf-8',
        dir=path.parent,
        prefix=f'.{path.name}.',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        writer = csv.DictWriter(temporary_file, fieldnames=TRANSACTION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically within its destination directory."""
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=path.parent,
        prefix=f'.{path.name}.',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(content)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy a source artifact atomically into the imports directory."""
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f'.{destination.name}.',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        shutil.copy2(source, temporary_path)
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read one YAML document whose root must be a mapping."""
    with path.open(encoding='utf-8') as source:
        document = yaml.safe_load(source)
    if not isinstance(document, dict):
        msg = f'{path}: document must be a mapping'
        raise TypeError(msg)
    return document
