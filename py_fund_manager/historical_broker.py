"""Deterministically fulfill normalized orders from historical price inputs."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from py_fund_manager.broker import SkippedOrder
from py_fund_manager.download import STOCKS_DIRECTORY
from py_fund_manager.rebalance import load_latest_daily_prices
from py_fund_manager.schemas import BrokerOrder, Execution, OrderSide

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


ONE_DOLLAR = Decimal('1.00')
STANDARD_PRICE_INCREMENT = Decimal('0.01')
SUB_DOLLAR_PRICE_INCREMENT = Decimal('0.0001')
ETRADE_QUANTITY_PRECISION = 3


class HistoricalBroker:
    """Fill orders from the historical price cache at a simulated execution time."""

    def __init__(
        self,
        executed_at: datetime,
        stocks_directory: Path = STOCKS_DIRECTORY,
        quantity_precision: int = ETRADE_QUANTITY_PRECISION,
    ) -> None:
        """Select the fill time, price cache, and supported share precision."""
        if executed_at.tzinfo is None:
            msg = 'historical execution time must include a UTC offset'
            raise ValueError(msg)
        if (
            not isinstance(quantity_precision, int)
            or isinstance(quantity_precision, bool)
            or quantity_precision < 0
        ):
            msg = 'historical broker quantity precision must be a nonnegative integer'
            raise ValueError(msg)
        self._executed_at = executed_at
        self._stocks_directory = stocks_directory
        self._quantity_increment = Decimal(1).scaleb(-quantity_precision)

    def prepare_order(self, order: BrokerOrder) -> BrokerOrder | SkippedOrder:
        """Adapt a planned quantity or omit an unsupported dust order."""
        if order.close_position:
            return order
        if order.side == OrderSide.BUY:
            quantity = order.quantity.quantize(
                self._quantity_increment,
                rounding=ROUND_DOWN,
            )
        else:
            quantity = order.quantity.quantize(
                self._quantity_increment,
                rounding=ROUND_CEILING,
            )
            if order.maximum_quantity is not None:
                maximum_quantity = order.maximum_quantity.quantize(
                    self._quantity_increment,
                    rounding=ROUND_DOWN,
                )
                if maximum_quantity <= 0:
                    return SkippedOrder(
                        order,
                        f'holding {order.maximum_quantity} cannot be represented at '
                        f'broker increment {self._quantity_increment}',
                    )
                quantity = min(quantity, maximum_quantity)
        if quantity <= 0:
            return SkippedOrder(
                order,
                f'quantity {order.quantity} cannot be represented at broker '
                f'increment {self._quantity_increment}',
            )
        return order.model_copy(update={'quantity': quantity})

    def execute_order(self, order: BrokerOrder) -> tuple[Execution, ...]:
        """Fill one complete order at its eligible historical observation."""
        if self._executed_at < order.submitted_at:
            msg = f'{order.ticker} historical execution predates order submission'
            raise ValueError(msg)
        observation = load_latest_daily_prices(
            {order.ticker},
            self._executed_at,
            order.currency,
            self._stocks_directory,
        )[order.ticker]
        price_increment = (
            STANDARD_PRICE_INCREMENT
            if observation.price >= ONE_DOLLAR
            else SUB_DOLLAR_PRICE_INCREMENT
        )
        price = observation.price.quantize(
            price_increment,
            rounding=ROUND_HALF_UP,
        )
        if price <= 0:
            msg = (
                f'{order.ticker} price {observation.price} is below the historical '
                f'broker increment {price_increment}'
            )
            raise ValueError(msg)
        return (
            Execution(
                id=f'{order.id}-fill-0001',
                order_id=order.id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                price=price,
                currency=order.currency,
                executed_at=self._executed_at,
            ),
        )
