"""Deterministically fulfill normalized orders from historical price inputs."""

from __future__ import annotations

from py_fund_manager.schemas import BrokerOrder, Execution, PriceObservation


class HistoricalBroker:
    """Fill orders at historical observations supplied by the simulation."""

    def __init__(self, prices: dict[str, PriceObservation]) -> None:
        """Initialize the adapter with ticker-keyed historical observations."""
        self._prices = dict(prices)

    def execute_order(self, order: BrokerOrder) -> tuple[Execution, ...]:
        """Fill one complete order at its eligible historical observation."""
        try:
            observation = self._prices[order.ticker]
        except KeyError as error:
            msg = f'no historical execution price for {order.ticker}'
            raise ValueError(msg) from error
        if observation.ticker != order.ticker:
            msg = f'{order.ticker} historical price contains {observation.ticker}'
            raise ValueError(msg)
        if observation.currency != order.currency:
            msg = (
                f'{order.ticker} historical price uses {observation.currency}; '
                f'order uses {order.currency}'
            )
            raise ValueError(msg)
        if observation.available_at > order.submitted_at:
            msg = f'{order.ticker} historical price was unavailable at order time'
            raise ValueError(msg)
        return (
            Execution(
                id=f'{order.id}-fill-0001',
                order_id=order.id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                price=observation.price,
                currency=order.currency,
                executed_at=order.submitted_at,
            ),
        )
