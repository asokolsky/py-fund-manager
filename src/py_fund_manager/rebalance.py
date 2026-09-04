"""Derive portfolio state and produce broker-neutral rebalance order plans."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from pathlib import Path  # noqa: TC003 - used by runtime-configurable storage paths.
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyarrow.parquet as pq
from exchange_calendars import get_calendar
from exchange_calendars.errors import (
    DateOutOfBounds,
    InvalidCalendarName,
    NotSessionError,
    RequestedSessionOutOfBounds,
)

from py_fund_manager.download import STOCKS_DIRECTORY, Interval, download, yahoo_ticker
from py_fund_manager.portfolio import (
    find_manifest_in,
    load_directory_manifests,
    load_transactions,
)
from py_fund_manager.schemas import (
    OrderReason,
    OrderSide,
    Portfolio,
    PriceObservation,
    RebalanceOrder,
    RebalancePlan,
    RebalanceSummary,
    RebalanceValuation,
    Strategy,
    StrategyAssignment,
    Transaction,
    TransactionType,
    normalize_cash_flow_amount,
)
from py_fund_manager.strategy import (
    effective_assignment,
    load_strategy_revision,
    strategy_revision,
)

CENT = Decimal('0.01')
QUANTITY_INCREMENT = Decimal('0.000001')
PRICE_PUBLICATION_DELAY = timedelta(minutes=15)
LEGACY_EXCHANGE_CALENDAR = 'legacy'


def derive_portfolio_state(
    portfolio: Portfolio,
    transactions: list[Transaction],
    as_of: datetime,
) -> tuple[dict[str, Decimal], Decimal]:
    """Derive aggregate positions and cash from confirmed ledger facts."""
    if as_of.tzinfo is None:
        msg = 'rebalance planning time must include a UTC offset'
        raise ValueError(msg)
    positions: dict[str, Decimal] = {}
    cash = Decimal(0)
    for transaction in transactions:
        if transaction.occurred_at > as_of:
            continue
        if transaction.currency != portfolio.spec.base_currency:
            msg = (
                f'transaction {transaction.id} uses {transaction.currency}; '
                f'portfolio base currency is {portfolio.spec.base_currency}'
            )
            raise ValueError(msg)
        ticker = transaction.ticker
        quantity = transaction.quantity
        if transaction.type in {
            TransactionType.OPENING_POSITION,
            TransactionType.BUY,
            TransactionType.TRANSFER_IN,
        }:
            _change_position(positions, ticker, quantity)
        elif transaction.type in {
            TransactionType.SELL,
            TransactionType.TRANSFER_OUT,
        }:
            _change_position(positions, ticker, -_required_quantity(transaction))
        elif transaction.type == TransactionType.POSITION_ADJUSTMENT:
            _change_position(positions, ticker, _required_quantity(transaction))
        elif transaction.type == TransactionType.SPLIT:
            msg = f'transaction {transaction.id}: split derivation is not implemented'
            raise ValueError(msg)

        if transaction.type in {
            TransactionType.OPENING_CASH,
            TransactionType.DEPOSIT,
            TransactionType.DIVIDEND,
            TransactionType.INTEREST,
        }:
            cash += _required_amount(transaction)
        elif transaction.type in {TransactionType.WITHDRAWAL, TransactionType.FEE}:
            cash -= _required_amount(transaction)
        elif transaction.type == TransactionType.BUY:
            cash -= _trade_amount(transaction)
        elif transaction.type == TransactionType.SELL:
            cash += _trade_amount(transaction)
        cash -= transaction.fees

    negative = {
        ticker: quantity for ticker, quantity in positions.items() if quantity < 0
    }
    if negative:
        tickers = ', '.join(sorted(negative))
        msg = f'ledger derives negative positions for: {tickers}'
        raise ValueError(msg)
    return (
        {ticker: quantity for ticker, quantity in positions.items() if quantity},
        cash,
    )


def load_latest_daily_prices(
    tickers: set[str],
    as_of: datetime,
    currency: str,
    stocks_directory: Path = STOCKS_DIRECTORY,
) -> dict[str, PriceObservation]:
    """Load the latest unadjusted daily close at or before a planning time."""
    observations: dict[str, PriceObservation] = {}
    for ticker in sorted(tickers):
        candidates: list[PriceObservation] = []
        price_tickers = [ticker]
        provider_ticker = yahoo_ticker(ticker)
        if provider_ticker != ticker:
            price_tickers.append(provider_ticker)
        for price_ticker in price_tickers:
            ticker_directory = (
                stocks_directory / 'interval=1d' / f'ticker={price_ticker}'
            )
            partitions: list[
                tuple[Path, dict[bytes, bytes], list[tuple[date, object]]]
            ] = []
            for path in sorted(ticker_directory.glob('year=*/data.parquet')):
                parquet = pq.ParquetFile(path)
                metadata = parquet.schema_arrow.metadata or {}
                table = parquet.read(columns=['date', 'close'])
                dates = table.column('date').to_pylist()
                closes = table.column('close').to_pylist()
                partitions.append(
                    (path, metadata, list(zip(dates, closes, strict=True)))
                )

            # Legacy partitions are deliberately read without their new provenance
            # fields until their date could be selected. The integrated refresh only
            # rewrites current and prior years, so validating every old partition
            # would break otherwise complete historical caches.
            candidate_dates = sorted(
                {
                    price_date
                    for _, _, rows in partitions
                    for price_date, close in rows
                    if close is not None and price_date <= as_of.date()
                },
                reverse=True,
            )
            for price_date in candidate_dates:
                date_candidates: list[PriceObservation] = []
                for path, metadata, rows in partitions:
                    for row_date, close in rows:
                        if row_date != price_date or close is None:
                            continue
                        stored_currency = _required_metadata(
                            metadata, b'currency', path
                        ).upper()
                        source = _required_metadata(metadata, b'source', path)
                        exchange_timezone = _required_metadata(
                            metadata, b'exchange_timezone', path
                        )
                        exchange_calendar, retrieved_at, available_at = (
                            _price_availability_provenance(
                                metadata, price_date, exchange_timezone, path
                            )
                        )
                        if available_at <= as_of:
                            date_candidates.append(
                                PriceObservation(
                                    ticker=ticker,
                                    as_of=price_date,
                                    available_at=available_at,
                                    price=Decimal(str(close)),
                                    currency=stored_currency,
                                    source=source,
                                    source_partition=path.relative_to(
                                        stocks_directory
                                    ).as_posix(),
                                    exchange_calendar=exchange_calendar,
                                    retrieved_at=retrieved_at,
                                )
                            )
                if date_candidates:
                    candidates.extend(date_candidates)
                    break
        if not candidates:
            msg = f'no daily price for {ticker} available at {as_of.isoformat()}'
            raise ValueError(msg)
        latest_available_at = max(candidate.available_at for candidate in candidates)
        latest = [
            candidate
            for candidate in candidates
            if candidate.available_at == latest_available_at
        ]
        distinct = {
            (candidate.price, candidate.currency, candidate.source)
            for candidate in latest
        }
        if len(distinct) != 1:
            conflicting_partitions = ', '.join(
                sorted(candidate.source_partition for candidate in latest)
            )
            msg = (
                f'conflicting latest daily prices for {ticker}: '
                f'{conflicting_partitions}'
            )
            raise ValueError(msg)
        observation = min(latest, key=lambda candidate: candidate.source_partition)
        if observation.currency != currency:
            msg = f'{ticker} price uses {observation.currency}; expected {currency}'
            raise ValueError(msg)
        observations[ticker] = observation
    return observations


def plan_rebalance(
    portfolio: Portfolio,
    transactions: list[Transaction],
    assignment: StrategyAssignment,
    strategy: Strategy,
    prices: dict[str, PriceObservation],
    *,
    as_of: datetime,
    withdrawal: Decimal = Decimal(0),
    generated_at: datetime | None = None,
    price_warnings: tuple[str, ...] = (),
) -> RebalancePlan:
    """Calculate a strict fractional-share rebalance order plan."""
    if strategy.metadata.name != assignment.strategy.name:
        msg = 'strategy does not match the effective assignment name'
        raise ValueError(msg)
    if strategy_revision(strategy) != assignment.strategy.revision:
        msg = 'strategy does not match the effective assignment revision'
        raise ValueError(msg)
    withdrawal = normalize_cash_flow_amount(withdrawal, 'withdrawal')
    positions, cash = derive_portfolio_state(portfolio, transactions, as_of)
    tickers = set(positions) | set(strategy.target_weights)
    missing_prices = sorted(tickers - prices.keys())
    if missing_prices:
        msg = f'missing prices for: {", ".join(missing_prices)}'
        raise ValueError(msg)
    for ticker in tickers:
        observation = prices[ticker]
        if observation.ticker != ticker:
            msg = f'price key {ticker} contains observation for {observation.ticker}'
            raise ValueError(msg)
        if observation.currency != portfolio.spec.base_currency:
            msg = (
                f'{ticker} price uses {observation.currency}; '
                f'expected {portfolio.spec.base_currency}'
            )
            raise ValueError(msg)
        if observation.available_at > as_of:
            msg = f'{ticker} price was not available at the planning time'
            raise ValueError(msg)

    current_values = {
        ticker: quantity * prices[ticker].price
        for ticker, quantity in positions.items()
    }
    holdings_value = sum(current_values.values(), Decimal(0))
    target_portfolio_value = holdings_value + cash - withdrawal
    if target_portfolio_value < 0:
        msg = 'withdrawal exceeds current portfolio value'
        raise ValueError(msg)

    orders: list[RebalanceOrder] = []
    for ticker in sorted(tickers):
        current_quantity = positions.get(ticker, Decimal(0))
        current_value = current_values.get(ticker, Decimal(0))
        target_weight = strategy.target_weights.get(ticker, Decimal(0))
        target_value = target_portfolio_value * target_weight
        difference = target_value - current_value
        target_notional = abs(difference)
        if target_notional < CENT:
            continue
        side = OrderSide.BUY if difference > 0 else OrderSide.SELL
        reason = (
            OrderReason.NOT_IN_STRATEGY
            if ticker not in strategy.target_weights
            else OrderReason.UNDERWEIGHT
            if side == OrderSide.BUY
            else OrderReason.OVERWEIGHT
        )
        price = prices[ticker]
        quantity_rounding = ROUND_DOWN if side == OrderSide.BUY else ROUND_CEILING
        quantity = (
            current_quantity
            if reason == OrderReason.NOT_IN_STRATEGY
            else (target_notional / price.price).quantize(
                QUANTITY_INCREMENT, rounding=quantity_rounding
            )
        )
        if side == OrderSide.SELL:
            quantity = min(quantity, current_quantity)
        if quantity <= 0:
            continue
        notional = quantity * price.price
        if notional <= 0:
            continue
        orders.append(
            RebalanceOrder(
                ticker=ticker,
                side=side,
                current_quantity=current_quantity,
                current_value=current_value.quantize(CENT),
                target_weight=target_weight,
                target_value=target_value.quantize(CENT),
                estimated_price=price.price,
                price_as_of=price.as_of,
                price_available_at=price.available_at,
                price_source=price.source,
                price_source_partition=price.source_partition,
                quantity=quantity,
                estimated_notional=notional,
                reason=reason,
            )
        )

    buys = sum(
        (order.estimated_notional for order in orders if order.side == OrderSide.BUY),
        Decimal(0),
    )
    sells = sum(
        (order.estimated_notional for order in orders if order.side == OrderSide.SELL),
        Decimal(0),
    )
    warnings = _plan_warnings(positions, strategy) + price_warnings
    return RebalancePlan(
        portfolio_id=portfolio.metadata.name,
        strategy_assignment_id=assignment.id,
        strategy=assignment.strategy,
        generated_at=generated_at or datetime.now(UTC),
        valuation=RebalanceValuation(
            as_of=as_of,
            currency=portfolio.spec.base_currency,
            holdings_value=holdings_value.quantize(CENT),
            available_cash=cash,
            withdrawal=withdrawal,
            target_portfolio_value=target_portfolio_value.quantize(CENT),
        ),
        orders=tuple(orders),
        summary=RebalanceSummary(
            buy_orders=sum(order.side == OrderSide.BUY for order in orders),
            sell_orders=sum(order.side == OrderSide.SELL for order in orders),
            estimated_buys=buys,
            estimated_sells=sells,
            estimated_ending_cash=(cash - withdrawal + sells - buys),
        ),
        warnings=warnings,
    )


def rebalance_portfolio(
    data_directory: Path,
    portfolio_id: str,
    as_of: datetime,
    *,
    withdrawal: Decimal = Decimal(0),
    stocks_directory: Path = STOCKS_DIRECTORY,
    allow_stale_prices: bool = False,
) -> RebalancePlan:
    """Refresh and validate prices before creating a rebalance order plan."""
    portfolio_directory = data_directory / 'portfolio' / portfolio_id
    manifests = load_directory_manifests(portfolio_directory)
    _, portfolio = find_manifest_in(
        portfolio_directory, manifests, 'Portfolio', expected_name=portfolio_id
    )
    transactions = load_transactions(portfolio_directory / 'transactions.csv')
    _, history = find_manifest_in(
        portfolio_directory, manifests, 'StrategyHistory', expected_name=portfolio_id
    )
    assignment = effective_assignment(history, as_of)
    strategy = load_strategy_revision(data_directory, assignment.strategy)
    positions, _ = derive_portfolio_state(portfolio, transactions, as_of)
    tickers = set(positions) | set(strategy.target_weights)
    provider_tickers = {yahoo_ticker(ticker) for ticker in tickers}
    download(
        provider_tickers,
        (as_of.year - 1, as_of.year),
        Interval.DAILY,
        stocks_directory=stocks_directory,
        progress_stream=sys.stderr,
    )
    prices = load_latest_daily_prices(
        tickers,
        as_of,
        portfolio.spec.base_currency,
        stocks_directory,
    )
    price_warnings = validate_price_freshness(
        prices, as_of, allow_stale=allow_stale_prices
    )
    return plan_rebalance(
        portfolio,
        transactions,
        assignment,
        strategy,
        prices,
        as_of=as_of,
        withdrawal=withdrawal,
        price_warnings=price_warnings,
    )


def expected_latest_session(
    exchange_calendar: str,
    as_of: datetime,
    publication_delay: timedelta = PRICE_PUBLICATION_DELAY,
) -> date:
    """Return the latest session whose close should have been published."""
    if as_of.tzinfo is None:
        msg = 'price freshness time must include a UTC offset'
        raise ValueError(msg)
    try:
        calendar = get_calendar(exchange_calendar)
        cutoff = as_of.astimezone(UTC) - publication_delay
        session = calendar.date_to_session(cutoff.date(), direction='previous')
        if calendar.session_close(session).to_pydatetime() > cutoff:
            session = calendar.previous_session(session)
    except (
        DateOutOfBounds,
        InvalidCalendarName,
        RequestedSessionOutOfBounds,
    ) as error:
        msg = f'cannot resolve exchange calendar {exchange_calendar}: {error}'
        raise ValueError(msg) from error
    return cast('date', session.date())


def validate_price_freshness(
    prices: dict[str, PriceObservation],
    as_of: datetime,
    *,
    allow_stale: bool = False,
) -> tuple[str, ...]:
    """Reject stale required prices or describe an explicit reviewed override."""
    stale: list[tuple[str, date, date | None]] = []
    for ticker, observation in sorted(prices.items()):
        if observation.exchange_calendar == LEGACY_EXCHANGE_CALENDAR:
            stale.append((ticker, observation.as_of, None))
            continue
        expected = expected_latest_session(observation.exchange_calendar, as_of)
        if observation.as_of < expected:
            stale.append((ticker, observation.as_of, expected))
    if not stale:
        return ()
    details = ', '.join(
        (
            f'{ticker} ({observed.isoformat()}; '
            'legacy cache lacks exchange-calendar provenance)'
            if expected is None
            else f'{ticker} ({observed.isoformat()}; expected {expected.isoformat()})'
        )
        for ticker, observed, expected in stale
    )
    if not allow_stale:
        raise ValueError(f'stale prices for: {details}')
    return (f'Explicitly allowed stale prices: {details}',)


def _change_position(
    positions: dict[str, Decimal], ticker: str | None, quantity: Decimal | None
) -> None:
    """Apply one validated position quantity change."""
    if ticker is None or quantity is None:
        msg = 'position-changing transaction requires ticker and quantity'
        raise ValueError(msg)
    positions[ticker] = positions.get(ticker, Decimal(0)) + quantity


def _required_metadata(metadata: dict[bytes, bytes], key: bytes, path: Path) -> str:
    """Read required nonempty UTF-8 metadata from one price partition."""
    try:
        value = metadata.get(key, b'').decode().strip()
    except UnicodeError as error:
        msg = f'{path}: metadata {key.decode()} must be UTF-8'
        raise ValueError(msg) from error
    if not value:
        msg = f'{path}: missing required metadata {key.decode()}'
        raise ValueError(msg)
    return value


def _required_datetime_metadata(
    metadata: dict[bytes, bytes], key: bytes, path: Path
) -> datetime:
    """Read required timezone-aware ISO datetime metadata."""
    value = _required_metadata(metadata, key, path)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        msg = f'{path}: metadata {key.decode()} must be an ISO datetime'
        raise ValueError(msg) from error
    if parsed.tzinfo is None:
        msg = f'{path}: metadata {key.decode()} must include a UTC offset'
        raise ValueError(msg)
    return parsed


def _price_availability_provenance(
    metadata: dict[bytes, bytes],
    price_date: date,
    exchange_timezone: str,
    path: Path,
) -> tuple[str, datetime, datetime]:
    """Qualify refreshed metadata or conservatively support a legacy cache row."""
    calendar_value = metadata.get(b'exchange_calendar')
    retrieved_value = metadata.get(b'retrieved_at_utc')
    if calendar_value is None and retrieved_value is None:
        available_at = _legacy_daily_close_available_at(
            price_date, exchange_timezone, path
        )
        return LEGACY_EXCHANGE_CALENDAR, available_at.astimezone(UTC), available_at
    if calendar_value is None or retrieved_value is None:
        msg = f'{path}: incomplete refreshed price provenance metadata'
        raise ValueError(msg)
    exchange_calendar = _required_metadata(metadata, b'exchange_calendar', path)
    retrieved_at = _required_datetime_metadata(metadata, b'retrieved_at_utc', path)
    available_at = _daily_close_available_at(
        price_date, exchange_timezone, exchange_calendar, path
    )
    return exchange_calendar, retrieved_at, available_at


def _legacy_daily_close_available_at(
    price_date: date, exchange_timezone: str, path: Path
) -> datetime:
    """Use the pre-refresh 16:00 local-close rule for a legacy partition."""
    try:
        timezone = ZoneInfo(exchange_timezone)
    except ZoneInfoNotFoundError as error:
        msg = f'{path}: unknown exchange timezone {exchange_timezone}'
        raise ValueError(msg) from error
    return datetime.combine(price_date, time(16), timezone)


def _daily_close_available_at(
    price_date: date,
    exchange_timezone: str,
    exchange_calendar: str,
    path: Path,
) -> datetime:
    """Return the exchange close plus provider-publication delay."""
    try:
        timezone = ZoneInfo(exchange_timezone)
    except ZoneInfoNotFoundError as error:
        msg = f'{path}: unknown exchange timezone {exchange_timezone}'
        raise ValueError(msg) from error
    try:
        calendar = get_calendar(exchange_calendar)
        close = calendar.session_close(price_date).to_pydatetime()
    except (InvalidCalendarName, NotSessionError) as error:
        msg = f'{path}: invalid {exchange_calendar} session {price_date}: {error}'
        raise ValueError(msg) from error
    return cast('datetime', (close + PRICE_PUBLICATION_DELAY).astimezone(timezone))


def _required_quantity(transaction: Transaction) -> Decimal:
    """Return a required transaction quantity."""
    if transaction.quantity is None:
        msg = f'transaction {transaction.id} requires quantity'
        raise ValueError(msg)
    return transaction.quantity


def _required_amount(transaction: Transaction) -> Decimal:
    """Return a required transaction cash amount."""
    if transaction.amount is None:
        msg = f'transaction {transaction.id} requires amount'
        raise ValueError(msg)
    return transaction.amount


def _trade_amount(transaction: Transaction) -> Decimal:
    """Return a trade's gross cash amount before fees."""
    if transaction.amount is not None:
        return transaction.amount
    if transaction.price is None or transaction.quantity is None:
        msg = f'transaction {transaction.id} requires amount or price and quantity'
        raise ValueError(msg)
    return transaction.price * transaction.quantity


def _plan_warnings(
    positions: dict[str, Decimal],
    strategy: Strategy,
) -> tuple[str, ...]:
    """Describe non-strategy positions affected by the plan."""
    warnings: list[str] = []
    non_strategy = sorted(set(positions) - set(strategy.target_weights))
    if non_strategy:
        warnings.append(
            'Positions absent from the strategy will be closed: '
            + ', '.join(non_strategy)
        )
    return tuple(warnings)
