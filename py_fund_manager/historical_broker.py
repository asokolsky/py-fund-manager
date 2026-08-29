"""Deterministically fulfill normalized orders from historical price inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from py_fund_manager.download import STOCKS_DIRECTORY
from py_fund_manager.rebalance import load_latest_daily_prices
from py_fund_manager.schemas import BrokerOrder, Execution

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


class HistoricalBroker:
    """Fill orders from the historical price cache at a simulated execution time."""

    def __init__(
        self,
        executed_at: datetime,
        stocks_directory: Path = STOCKS_DIRECTORY,
    ) -> None:
        """Select the simulated fill time and historical price cache."""
        if executed_at.tzinfo is None:
            msg = 'historical execution time must include a UTC offset'
            raise ValueError(msg)
        self._executed_at = executed_at
        self._stocks_directory = stocks_directory

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
        return (
            Execution(
                id=f'{order.id}-fill-0001',
                order_id=order.id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                price=observation.price,
                currency=order.currency,
                executed_at=self._executed_at,
            ),
        )
