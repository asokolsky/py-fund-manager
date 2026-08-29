"""Validated schemas for portfolios, transactions, and strategies."""

from __future__ import annotations

import re
from datetime import date, datetime  # noqa: TC003 - Pydantic resolves these at runtime.
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WEIGHT_TOLERANCE = Decimal('0.000001')
PORTFOLIO_ID_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
RESOURCE_NAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
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


class ObjectMetadata(BaseModel):
    """Stable resource identity shared by all YAML manifests."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, pattern=RESOURCE_NAME_PATTERN)


class DisplayMetadata(ObjectMetadata):
    """Resource identity with a required human-readable presentation name."""

    display_name: str = Field(min_length=1)


class PortfolioSpec(BaseModel):
    """Desired configuration for one investment account."""

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        str_strip_whitespace=True,
    )

    broker: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    base_currency: str = Field(min_length=3, max_length=3)

    @field_validator('base_currency', mode='before')
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        """Normalize a serialized ISO currency code."""
        return value.strip().upper() if isinstance(value, str) else value


class Portfolio(BaseModel):
    """Versioned manifest for one investment account."""

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        str_strip_whitespace=True,
        validate_by_name=True,
    )

    api_version: Literal['v1'] = Field(alias='apiVersion')
    kind: Literal['Portfolio']
    metadata: DisplayMetadata
    spec: PortfolioSpec


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
    def validate_transaction_shape(self) -> Self:
        """Require the fields needed to derive each supported ledger fact."""
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
        cash_events = {
            TransactionType.OPENING_CASH,
            TransactionType.DEPOSIT,
            TransactionType.WITHDRAWAL,
            TransactionType.DIVIDEND,
            TransactionType.INTEREST,
            TransactionType.FEE,
        }
        if self.type in cash_events and self.amount is None:
            msg = f'{self.type} requires amount'
            raise ValueError(msg)
        if self.type in {TransactionType.BUY, TransactionType.SELL} and (
            self.amount is None and self.price is None
        ):
            msg = f'{self.type} requires amount or price'
            raise ValueError(msg)
        if self.type == TransactionType.OPENING_CASH and any(
            value is not None
            for value in (self.ticker, self.quantity, self.price, self.cost_basis)
        ):
            msg = 'opening_cash accepts only amount, currency, and identity fields'
            raise ValueError(msg)
        if self.type == TransactionType.OPENING_POSITION and any(
            value is not None for value in (self.price, self.amount)
        ):
            msg = 'opening_position does not accept price or amount'
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


class StrategySpec(BaseModel):
    """Desired target allocation for an investment strategy."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    allocation: TargetAllocation
    benchmark: str | None = None


class Strategy(BaseModel):
    """Versioned manifest for a target allocation strategy."""

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        str_strip_whitespace=True,
        validate_by_name=True,
    )

    api_version: Literal['v1'] = Field(alias='apiVersion')
    kind: Literal['Strategy']
    metadata: DisplayMetadata
    spec: StrategySpec

    @property
    def target_weights(self) -> dict[str, Decimal]:
        """Expose target weights directly for allocation calculations."""
        return self.spec.allocation.positions


class StrategyAnalysis(BaseModel):
    """Typed summary of one validated strategy."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    name: str
    display_name: str
    benchmark: str | None
    allocation_type: Literal['target_weights']
    position_count: int = Field(ge=1)
    total_weight: Decimal


class StrategyRevisionReference(BaseModel):
    """Identity of one immutable strategy revision."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, pattern=RESOURCE_NAME_PATTERN)
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


class StrategyHistorySpec(BaseModel):
    """Effective-dated Strategy assignments for one Portfolio."""

    model_config = ConfigDict(extra='forbid', frozen=True)

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


class StrategyHistory(BaseModel):
    """Versioned manifest of Strategy assignments for one Portfolio."""

    model_config = ConfigDict(extra='forbid', frozen=True, validate_by_name=True)

    api_version: Literal['v1'] = Field(alias='apiVersion')
    kind: Literal['StrategyHistory']
    metadata: ObjectMetadata
    spec: StrategyHistorySpec


class PriceObservation(BaseModel):
    """One market price and the partition metadata that qualifies its use."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    ticker: str = Field(min_length=1, pattern=TICKER_PATTERN)
    as_of: date
    available_at: datetime
    price: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    source: str = Field(min_length=1)
    source_partition: str = Field(min_length=1)

    @field_validator('ticker', 'currency', mode='before')
    @classmethod
    def normalize_price_code(cls, value: object) -> object:
        """Normalize ticker and currency codes in price observations."""
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode='after')
    def validate_availability(self) -> Self:
        """Require an aware availability time on the observation date."""
        if self.available_at.tzinfo is None:
            msg = 'price available_at must include a UTC offset'
            raise ValueError(msg)
        if self.available_at.date() != self.as_of:
            msg = 'price available_at must fall on the observation date'
            raise ValueError(msg)
        return self


class OrderSide(StrEnum):
    """Direction of a proposed rebalance order."""

    BUY = 'buy'
    SELL = 'sell'


class OrderReason(StrEnum):
    """Reason a proposed rebalance order is needed."""

    UNDERWEIGHT = 'underweight'
    OVERWEIGHT = 'overweight'
    NOT_IN_STRATEGY = 'not_in_strategy'


class BrokerOrder(BaseModel):
    """Transport-neutral order submitted to a broker adapter."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    ticker: str = Field(min_length=1, pattern=TICKER_PATTERN)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    submitted_at: datetime

    @field_validator('ticker', 'currency', mode='before')
    @classmethod
    def normalize_order_code(cls, value: object) -> object:
        """Normalize order ticker and currency codes."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator('submitted_at')
    @classmethod
    def validate_submitted_at(cls, value: datetime) -> datetime:
        """Require an unambiguous order submission time."""
        if value.tzinfo is None:
            msg = 'order submitted_at must include a UTC offset'
            raise ValueError(msg)
        return value


class Execution(BaseModel):
    """One confirmed full or partial fill returned by a broker adapter."""

    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1, pattern=TICKER_PATTERN)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)
    currency: str = Field(min_length=3, max_length=3)
    executed_at: datetime

    @field_validator('ticker', 'currency', mode='before')
    @classmethod
    def normalize_execution_code(cls, value: object) -> object:
        """Normalize execution ticker and currency codes."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator('executed_at')
    @classmethod
    def validate_executed_at(cls, value: datetime) -> datetime:
        """Require an unambiguous execution time."""
        if value.tzinfo is None:
            msg = 'execution executed_at must include a UTC offset'
            raise ValueError(msg)
        return value


class RebalanceOrder(BaseModel):
    """Broker-neutral intent for one security trade."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    ticker: str = Field(min_length=1)
    side: OrderSide
    current_quantity: Decimal = Field(ge=0)
    current_value: Decimal = Field(ge=0)
    target_weight: Decimal = Field(ge=0, le=1)
    target_value: Decimal = Field(ge=0)
    estimated_price: Decimal = Field(gt=0)
    price_as_of: date
    price_available_at: datetime
    price_source: str = Field(min_length=1)
    price_source_partition: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    estimated_notional: Decimal = Field(gt=0)
    reason: OrderReason

    @model_validator(mode='after')
    def validate_execution_estimate(self) -> Self:
        """Require coherent price provenance and executable arithmetic."""
        if self.price_available_at.tzinfo is None:
            msg = 'price_available_at must include a UTC offset'
            raise ValueError(msg)
        if self.price_available_at.date() != self.price_as_of:
            msg = 'price availability must fall on price_as_of'
            raise ValueError(msg)
        if self.estimated_notional != self.quantity * self.estimated_price:
            msg = 'estimated_notional must equal quantity times estimated_price'
            raise ValueError(msg)
        if self.side == OrderSide.SELL and self.quantity > self.current_quantity:
            msg = 'sell quantity must not exceed current quantity'
            raise ValueError(msg)
        return self


class RebalanceValuation(BaseModel):
    """Portfolio values and assumed cash flow used by a rebalance plan."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    as_of: datetime
    currency: str = Field(min_length=3, max_length=3)
    holdings_value: Decimal = Field(ge=0)
    available_cash: Decimal
    contribution: Decimal = Field(default=Decimal(0), ge=0)
    withdrawal: Decimal = Field(default=Decimal(0), ge=0)
    target_portfolio_value: Decimal = Field(ge=0)

    @field_validator('as_of')
    @classmethod
    def validate_as_of(cls, value: datetime) -> datetime:
        """Require an unambiguous valuation time."""
        if value.tzinfo is None:
            msg = 'valuation as_of must include a UTC offset'
            raise ValueError(msg)
        return value


class RebalanceSummary(BaseModel):
    """Aggregate order counts and estimated cash result."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    buy_orders: int = Field(ge=0)
    sell_orders: int = Field(ge=0)
    estimated_buys: Decimal = Field(ge=0)
    estimated_sells: Decimal = Field(ge=0)
    estimated_ending_cash: Decimal = Field(ge=0)


class RebalancePlan(BaseModel):
    """Validated broker-neutral output of portfolio rebalance planning."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: Literal[2] = 2
    portfolio_id: str = Field(min_length=1)
    strategy_assignment_id: str = Field(min_length=1)
    strategy: StrategyRevisionReference
    generated_at: datetime
    valuation: RebalanceValuation
    orders: tuple[RebalanceOrder, ...]
    summary: RebalanceSummary
    warnings: tuple[str, ...] = ()

    @field_validator('generated_at')
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        """Require an unambiguous plan generation time."""
        if value.tzinfo is None:
            msg = 'generated_at must include a UTC offset'
            raise ValueError(msg)
        return value

    @model_validator(mode='after')
    def validate_summary(self) -> Self:
        """Require summary counts and cash arithmetic to match the orders."""
        buys = [order for order in self.orders if order.side == OrderSide.BUY]
        sells = [order for order in self.orders if order.side == OrderSide.SELL]
        estimated_buys = sum((order.estimated_notional for order in buys), Decimal(0))
        estimated_sells = sum((order.estimated_notional for order in sells), Decimal(0))
        expected_cash = (
            self.valuation.available_cash
            + self.valuation.contribution
            - self.valuation.withdrawal
            + estimated_sells
            - estimated_buys
        )
        expected = (
            len(buys),
            len(sells),
            estimated_buys,
            estimated_sells,
            expected_cash,
        )
        actual = (
            self.summary.buy_orders,
            self.summary.sell_orders,
            self.summary.estimated_buys,
            self.summary.estimated_sells,
            self.summary.estimated_ending_cash,
        )
        if actual != expected:
            msg = 'rebalance summary must reconcile exactly to its orders'
            raise ValueError(msg)
        return self


MAX_CURRENCY_INTEGER_DIGITS = 18


def normalize_cash_flow_amount(value: Decimal, name: str = 'amount') -> Decimal:
    """Validate a nonnegative currency amount and express it exactly in cents."""
    if not value.is_finite() or value < 0:
        msg = f'{name} must be a finite nonnegative decimal number'
        raise ValueError(msg)
    sign, digits_tuple, exponent_value = value.as_tuple()
    exponent = cast('int', exponent_value)  # Finite Decimals always use int here.
    if not any(digits_tuple):
        return Decimal('0.00')
    integer_digits = max(len(digits_tuple) + exponent, 0)
    if integer_digits > MAX_CURRENCY_INTEGER_DIGITS:
        msg = f'{name} exceeds the {MAX_CURRENCY_INTEGER_DIGITS}-digit limit'
        raise ValueError(msg)
    digits = list(digits_tuple)
    while exponent < -2 and digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -2:
        msg = f'{name} must not have fractions smaller than one cent'
        raise ValueError(msg)
    if exponent > -2:
        digits.extend([0] * (exponent + 2))
        exponent = -2
    return Decimal((sign, tuple(digits), exponent))
