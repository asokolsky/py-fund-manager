"""Derive portfolio state and produce broker-neutral rebalance order plans."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path  # noqa: TC003 - used by runtime-configurable storage paths.

import pyarrow.parquet as pq

from py_fund_manager.download import STOCKS_DIRECTORY
from py_fund_manager.portfolio import (
    load_portfolio,
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
)
from py_fund_manager.strategy import (
    effective_assignment,
    load_strategy_history,
    load_strategy_revision,
    strategy_revision,
)

CENT = Decimal('0.01')
QUANTITY_INCREMENT = Decimal('0.000001')


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
        if transaction.currency != portfolio.base_currency:
            msg = (
                f'transaction {transaction.id} uses {transaction.currency}; '
                f'portfolio base currency is {portfolio.base_currency}'
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
    requested_date = as_of.date()
    for ticker in sorted(tickers):
        latest_date: date | None = None
        latest_price: Decimal | None = None
        latest_currency = currency
        price_tickers = [ticker]
        yahoo_ticker = ticker.replace('.', '-')
        if yahoo_ticker != ticker:
            price_tickers.append(yahoo_ticker)
        for price_ticker in price_tickers:
            ticker_directory = (
                stocks_directory / 'interval=1d' / f'ticker={price_ticker}'
            )
            for path in ticker_directory.glob('year=*/data.parquet'):
                parquet = pq.ParquetFile(path)
                metadata = parquet.schema_arrow.metadata or {}
                stored_currency = metadata.get(b'currency', b'').decode()
                if stored_currency:
                    latest_currency = stored_currency.upper()
                table = parquet.read(columns=['date', 'close'])
                dates = table.column('date').to_pylist()
                closes = table.column('close').to_pylist()
                for price_date, close in zip(dates, closes, strict=True):
                    if price_date > requested_date or close is None:
                        continue
                    if latest_date is None or price_date > latest_date:
                        latest_date = price_date
                        latest_price = Decimal(str(close))
        if latest_date is None or latest_price is None:
            msg = f'no daily price for {ticker} at or before {requested_date}'
            raise ValueError(msg)
        if latest_currency != currency:
            msg = f'{ticker} price uses {latest_currency}; expected {currency}'
            raise ValueError(msg)
        observations[ticker] = PriceObservation(
            ticker=ticker,
            as_of=latest_date,
            price=latest_price,
            currency=latest_currency,
        )
    return observations


def plan_rebalance(
    portfolio: Portfolio,
    transactions: list[Transaction],
    assignment: StrategyAssignment,
    strategy: Strategy,
    prices: dict[str, PriceObservation],
    *,
    as_of: datetime,
    contribution: Decimal = Decimal(0),
    withdrawal: Decimal = Decimal(0),
    generated_at: datetime | None = None,
) -> RebalancePlan:
    """Calculate a strict fractional-share rebalance order plan."""
    if strategy.id != assignment.strategy.id:
        msg = 'strategy does not match the effective assignment ID'
        raise ValueError(msg)
    if strategy_revision(strategy) != assignment.strategy.revision:
        msg = 'strategy does not match the effective assignment revision'
        raise ValueError(msg)
    if contribution < 0 or withdrawal < 0:
        msg = 'contribution and withdrawal must be nonnegative'
        raise ValueError(msg)
    if contribution and withdrawal:
        msg = 'contribution and withdrawal are mutually exclusive'
        raise ValueError(msg)
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
        if observation.currency != portfolio.base_currency:
            msg = (
                f'{ticker} price uses {observation.currency}; '
                f'expected {portfolio.base_currency}'
            )
            raise ValueError(msg)

    current_values = {
        ticker: quantity * prices[ticker].price
        for ticker, quantity in positions.items()
    }
    holdings_value = sum(current_values.values(), Decimal(0))
    target_portfolio_value = holdings_value + cash + contribution - withdrawal
    if target_portfolio_value < 0:
        msg = 'withdrawal exceeds current portfolio value plus contribution'
        raise ValueError(msg)

    orders: list[RebalanceOrder] = []
    for ticker in sorted(tickers):
        current_quantity = positions.get(ticker, Decimal(0))
        current_value = current_values.get(ticker, Decimal(0))
        target_weight = strategy.target_weights.get(ticker, Decimal(0))
        target_value = target_portfolio_value * target_weight
        difference = target_value - current_value
        notional = abs(difference).quantize(CENT)
        if notional < CENT:
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
        quantity = (
            current_quantity
            if reason == OrderReason.NOT_IN_STRATEGY
            else (notional / price.price).quantize(
                QUANTITY_INCREMENT, rounding=ROUND_DOWN
            )
        )
        if quantity <= 0:
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
    warnings = _plan_warnings(positions, strategy, prices, as_of)
    return RebalancePlan(
        portfolio_id=portfolio.id,
        strategy_assignment_id=assignment.id,
        strategy=assignment.strategy,
        generated_at=generated_at or datetime.now(UTC),
        valuation=RebalanceValuation(
            as_of=as_of,
            currency=portfolio.base_currency,
            holdings_value=holdings_value.quantize(CENT),
            available_cash=cash.quantize(CENT),
            contribution=contribution.quantize(CENT),
            withdrawal=withdrawal.quantize(CENT),
            target_portfolio_value=target_portfolio_value.quantize(CENT),
        ),
        orders=tuple(orders),
        summary=RebalanceSummary(
            buy_orders=sum(order.side == OrderSide.BUY for order in orders),
            sell_orders=sum(order.side == OrderSide.SELL for order in orders),
            estimated_buys=buys.quantize(CENT),
            estimated_sells=sells.quantize(CENT),
            estimated_ending_cash=(
                cash + contribution - withdrawal + sells - buys
            ).quantize(CENT),
        ),
        warnings=warnings,
    )


def rebalance_portfolio(
    data_directory: Path,
    portfolio_id: str,
    as_of: datetime,
    *,
    contribution: Decimal = Decimal(0),
    withdrawal: Decimal = Decimal(0),
    stocks_directory: Path = STOCKS_DIRECTORY,
) -> RebalancePlan:
    """Load all portfolio inputs and create its rebalance order plan."""
    portfolio_directory = data_directory / 'portfolios' / portfolio_id
    portfolio = load_portfolio(portfolio_directory / 'portfolio.yaml')
    if portfolio.id != portfolio_id:
        msg = f'expected portfolio ID {portfolio_id}, got {portfolio.id}'
        raise ValueError(msg)
    transactions = load_transactions(portfolio_directory / 'transactions.csv')
    history = load_strategy_history(portfolio_directory / 'strategy-history.yaml')
    assignment = effective_assignment(history, as_of)
    strategy = load_strategy_revision(data_directory, assignment.strategy)
    positions, _ = derive_portfolio_state(portfolio, transactions, as_of)
    prices = load_latest_daily_prices(
        set(positions) | set(strategy.target_weights),
        as_of,
        portfolio.base_currency,
        stocks_directory,
    )
    return plan_rebalance(
        portfolio,
        transactions,
        assignment,
        strategy,
        prices,
        as_of=as_of,
        contribution=contribution,
        withdrawal=withdrawal,
    )


def _change_position(
    positions: dict[str, Decimal], ticker: str | None, quantity: Decimal | None
) -> None:
    """Apply one validated position quantity change."""
    if ticker is None or quantity is None:
        msg = 'position-changing transaction requires ticker and quantity'
        raise ValueError(msg)
    positions[ticker] = positions.get(ticker, Decimal(0)) + quantity


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
    prices: dict[str, PriceObservation],
    as_of: datetime,
) -> tuple[str, ...]:
    """Describe non-strategy positions and stale valuation observations."""
    warnings: list[str] = []
    non_strategy = sorted(set(positions) - set(strategy.target_weights))
    if non_strategy:
        warnings.append(
            'Positions absent from the strategy will be closed: '
            + ', '.join(non_strategy)
        )
    stale = sorted(
        ticker
        for ticker, observation in prices.items()
        if observation.as_of < as_of.date()
    )
    if stale:
        oldest = min(prices[ticker].as_of for ticker in stale)
        warnings.append(
            f'Prices are older than the planning date for {len(stale)} ticker(s); '
            f'the oldest is {oldest.isoformat()}'
        )
    return tuple(warnings)
