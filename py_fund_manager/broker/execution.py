"""Transport-neutral broker contracts and rebalance execution services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

from py_fund_manager.portfolio import execution_transaction, validate_transaction_ledger
from py_fund_manager.rebalance import derive_portfolio_state
from py_fund_manager.schemas import (
    BrokerOrder,
    Execution,
    OrderSide,
    Portfolio,
    RebalanceOrder,
    RebalancePlan,
    Transaction,
)


@dataclass(frozen=True)
class SkippedOrder:
    """A planned broker order omitted during adapter preparation."""

    order: BrokerOrder
    reason: str


class Broker(Protocol):
    """Fulfill normalized orders without owning portfolio ledger concerns."""

    def prepare_order(self, order: BrokerOrder) -> BrokerOrder | SkippedOrder:
        """Adapt a planned order to the broker's supported order contract."""
        ...

    def execute_order(self, order: BrokerOrder) -> tuple[Execution, ...]:
        """Submit one order and return its confirmed fills."""
        ...


@dataclass(frozen=True)
class RebalanceExecutionResult:
    """Normalized orders, confirmed fills, and ledger facts for one plan."""

    orders: tuple[BrokerOrder, ...]
    executions: tuple[Execution, ...]
    transactions: tuple[Transaction, ...]
    skipped_orders: tuple[SkippedOrder, ...]


def execute_rebalance_plan(
    broker: Broker,
    portfolio: Portfolio,
    transactions: list[Transaction] | tuple[Transaction, ...],
    plan: RebalancePlan,
    on_order_skipped: Callable[[SkippedOrder], None] | None = None,
) -> RebalanceExecutionResult:
    """Validate and execute a reviewed plan through any conforming broker."""
    positions = _validate_plan_inputs(portfolio, transactions, plan)
    orders: list[BrokerOrder] = []
    skipped_orders: list[SkippedOrder] = []
    for index, planned_order in enumerate(plan.orders, start=1):
        order = _broker_order(plan, planned_order, index)
        prepared_order = broker.prepare_order(order)
        if isinstance(prepared_order, SkippedOrder):
            skipped_orders.append(prepared_order)
            if on_order_skipped is not None:
                on_order_skipped(prepared_order)
        else:
            orders.append(prepared_order)
    executions: list[Execution] = []
    for order in orders:
        fills = broker.execute_order(order)
        _validate_fills(order, fills)
        executions.extend(fills)

    facts = list(map(execution_transaction, executions))
    facts.sort(key=lambda transaction: transaction.occurred_at)
    candidate = [*transactions, *facts]
    validate_transaction_ledger(candidate)
    final_time = facts[-1].occurred_at if facts else plan.valuation.as_of
    final_positions, final_cash = derive_portfolio_state(
        portfolio, candidate, final_time
    )
    required_cash = plan.valuation.withdrawal
    if final_cash < required_cash:
        msg = (
            'broker executions leave insufficient cash for the planned withdrawal: '
            f'{final_cash} < {required_cash}'
        )
        raise ValueError(msg)
    _validate_expected_positions(positions, executions, final_positions)
    return RebalanceExecutionResult(
        tuple(orders),
        tuple(executions),
        tuple(facts),
        tuple(skipped_orders),
    )


def _validate_plan_inputs(
    portfolio: Portfolio,
    transactions: list[Transaction] | tuple[Transaction, ...],
    plan: RebalancePlan,
) -> dict[str, Decimal]:
    """Reject plans that no longer match their portfolio ledger inputs."""
    validate_transaction_ledger(transactions)
    if transactions and transactions[-1].occurred_at > plan.valuation.as_of:
        msg = (
            f'ledger contains transaction {transactions[-1].id} after plan time '
            f'{plan.valuation.as_of.isoformat()}'
        )
        raise ValueError(msg)
    if plan.portfolio_id != portfolio.metadata.name:
        msg = f'plan belongs to {plan.portfolio_id}; expected {portfolio.metadata.name}'
        raise ValueError(msg)
    if plan.valuation.currency != portfolio.spec.base_currency:
        msg = (
            f'plan uses {plan.valuation.currency}; '
            f'portfolio uses {portfolio.spec.base_currency}'
        )
        raise ValueError(msg)
    positions, available_cash = derive_portfolio_state(
        portfolio, list(transactions), plan.valuation.as_of
    )
    if available_cash != plan.valuation.available_cash:
        msg = (
            'plan available cash does not match the ledger: '
            f'{plan.valuation.available_cash} != {available_cash}'
        )
        raise ValueError(msg)
    for order in plan.orders:
        current_quantity = positions.get(order.ticker, Decimal(0))
        if current_quantity != order.current_quantity:
            msg = (
                f'{order.ticker} plan quantity does not match the ledger: '
                f'{order.current_quantity} != {current_quantity}'
            )
            raise ValueError(msg)
        if order.price_available_at > plan.valuation.as_of:
            msg = f'{order.ticker} plan price was unavailable at planning time'
            raise ValueError(msg)
    return positions


def _broker_order(
    plan: RebalancePlan, order: RebalanceOrder, index: int
) -> BrokerOrder:
    """Convert one rebalance intent into a transport-neutral broker order."""
    prefix = plan.valuation.as_of.astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    return BrokerOrder(
        id=f'{plan.portfolio_id}-{prefix}-{index:04d}',
        ticker=order.ticker,
        side=order.side,
        quantity=order.quantity,
        maximum_quantity=(
            order.current_quantity if order.side == OrderSide.SELL else None
        ),
        close_position=(
            order.side == OrderSide.SELL and order.quantity == order.current_quantity
        ),
        currency=plan.valuation.currency,
        submitted_at=plan.valuation.as_of,
    )


def _validate_fills(order: BrokerOrder, fills: tuple[Execution, ...]) -> None:
    """Require coherent fills that exactly complete the submitted order."""
    if not fills:
        msg = f'broker returned no executions for order {order.id}'
        raise ValueError(msg)
    fill_ids = [fill.id for fill in fills]
    if len(fill_ids) != len(set(fill_ids)):
        msg = f'broker returned duplicate execution IDs for order {order.id}'
        raise ValueError(msg)
    for fill in fills:
        if fill.order_id != order.id:
            msg = f'execution {fill.id} belongs to unexpected order {fill.order_id}'
            raise ValueError(msg)
        if (fill.ticker, fill.side, fill.currency) != (
            order.ticker,
            order.side,
            order.currency,
        ):
            msg = f'execution {fill.id} does not match order {order.id}'
            raise ValueError(msg)
        if fill.executed_at < order.submitted_at:
            msg = f'execution {fill.id} predates order {order.id}'
            raise ValueError(msg)
    filled_quantity = sum((fill.quantity for fill in fills), Decimal(0))
    if filled_quantity != order.quantity:
        msg = f'order {order.id} filled {filled_quantity}; expected {order.quantity}'
        raise ValueError(msg)


def _validate_expected_positions(
    initial: dict[str, Decimal],
    executions: list[Execution],
    actual: dict[str, Decimal],
) -> None:
    """Require derived positions to reflect confirmed executions exactly."""
    expected = dict(initial)
    for execution in executions:
        change = (
            execution.quantity
            if execution.side == OrderSide.BUY
            else -execution.quantity
        )
        expected[execution.ticker] = expected.get(execution.ticker, Decimal(0)) + change
    expected = {ticker: quantity for ticker, quantity in expected.items() if quantity}
    if actual != expected:
        msg = 'broker execution state does not reconcile'
        raise ValueError(msg)
