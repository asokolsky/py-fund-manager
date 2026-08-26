"""Validated schemas for portfolios, transactions, and strategies."""

from __future__ import annotations

import re
from datetime import date, datetime  # noqa: TC003 - Pydantic resolves these at runtime.
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WEIGHT_TOLERANCE = Decimal('0.000001')
PORTFOLIO_ID_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
TICKER_PATTERN = re.compile(r'^[A-Z0-9][A-Z0-9.^=-]*$')
REVISION_PATTERN = re.compile(r'^sha256:[0-9a-f]{64}$')


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


class StrategyRevisionReference(BaseModel):
    """Identity of one immutable strategy revision."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    revision: str = Field(pattern=REVISION_PATTERN)


class StrategyAssignment(BaseModel):
    """Effective-dated association between a portfolio and strategy revision."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    effective_at: datetime
    strategy: StrategyRevisionReference
    reason: str | None = Field(default=None, min_length=1)

    @field_validator('effective_at')
    @classmethod
    def validate_effective_at(cls, value: datetime) -> datetime:
        """Require an unambiguous assignment effective time."""
        if value.tzinfo is None:
            msg = 'effective_at must include a UTC offset'
            raise ValueError(msg)
        return value


class StrategyHistory(BaseModel):
    """Ordered append-only strategy assignments for one portfolio."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: Literal[1] = 1
    assignments: tuple[StrategyAssignment, ...] = Field(min_length=1)

    @field_validator('assignments')
    @classmethod
    def validate_assignments(
        cls, value: tuple[StrategyAssignment, ...]
    ) -> tuple[StrategyAssignment, ...]:
        """Require unique identities and strictly increasing effective times."""
        ids = [assignment.id for assignment in value]
        if len(ids) != len(set(ids)):
            msg = 'strategy assignment IDs must be unique'
            raise ValueError(msg)
        effective_times = [assignment.effective_at for assignment in value]
        if any(current <= previous for previous, current in pairwise(effective_times)):
            msg = 'strategy assignments must have increasing effective times'
            raise ValueError(msg)
        return value
