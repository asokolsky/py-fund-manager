"""Read-only TUI portfolio browser for effective-dated valuations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Label, OptionList, Static

from py_fund_manager.portfolio import find_manifest, load_transactions
from py_fund_manager.rebalance import (
    STOCKS_DIRECTORY,
    derive_portfolio_state,
    load_latest_daily_prices,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from textual.binding import BindingType


@dataclass(frozen=True)
class PositionSnapshot:
    """Quantity and cached market value for one effective position."""

    ticker: str
    quantity: Decimal
    value: Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Validated portfolio metadata and valuation at one point in time."""

    portfolio_id: str
    display_name: str
    broker: str
    account_id: str
    base_currency: str
    as_of: datetime
    positions: tuple[PositionSnapshot, ...]
    cash: Decimal
    positions_value: Decimal
    total_value: Decimal
    transaction_count: int
    warnings: tuple[str, ...]


def load_portfolio_snapshots(
    data_directory: Path,
    portfolio_id: str | None,
    as_of: datetime,
    stocks_directory: Path = STOCKS_DIRECTORY,
) -> tuple[PortfolioSnapshot, ...]:
    """Load and value all portfolios or one explicitly selected portfolio."""
    directories = _portfolio_directories(data_directory, portfolio_id)

    snapshots: list[PortfolioSnapshot] = []
    for directory in directories:
        _, portfolio = find_manifest(
            directory, 'Portfolio', expected_name=directory.name
        )
        ledger = directory / 'transactions.csv'
        transactions = load_transactions(ledger) if ledger.exists() else []
        quantities, cash = derive_portfolio_state(portfolio, transactions, as_of)
        prices = load_latest_daily_prices(
            set(quantities), as_of, portfolio.spec.base_currency, stocks_directory
        )
        positions = tuple(
            PositionSnapshot(ticker, quantity, quantity * prices[ticker].price)
            for ticker, quantity in sorted(quantities.items())
        )
        positions_value = sum(
            (position.value for position in positions), start=Decimal(0)
        )
        stale = sorted(
            ticker
            for ticker, observation in prices.items()
            if observation.as_of < as_of.date()
        )
        warnings = (
            (
                f'Prices are older than {as_of.date().isoformat()} for: '
                + ', '.join(stale),
            )
            if stale
            else ()
        )
        snapshots.append(
            PortfolioSnapshot(
                portfolio_id=portfolio.metadata.name,
                display_name=portfolio.metadata.display_name,
                broker=portfolio.spec.broker,
                account_id=portfolio.spec.account_id,
                base_currency=portfolio.spec.base_currency,
                as_of=as_of,
                positions=positions,
                cash=cash,
                positions_value=positions_value,
                total_value=cash + positions_value,
                transaction_count=sum(
                    transaction.occurred_at <= as_of for transaction in transactions
                ),
                warnings=warnings,
            )
        )
    return tuple(snapshots)


def load_portfolio_timestamps(
    data_directory: Path, portfolio_id: str | None
) -> tuple[datetime, ...]:
    """Load unique transaction timestamps for one portfolio or their union."""
    timestamps: set[datetime] = set()
    for directory in _portfolio_directories(data_directory, portfolio_id):
        ledger = directory / 'transactions.csv'
        if ledger.exists():
            timestamps.update(
                transaction.occurred_at for transaction in load_transactions(ledger)
            )
    return tuple(sorted(timestamps, reverse=True))


def latest_portfolio_timestamp(
    timestamps: tuple[datetime, ...], reference: datetime
) -> datetime:
    """Return the latest transaction timestamp not later than a reference time."""
    eligible = tuple(timestamp for timestamp in timestamps if timestamp <= reference)
    if not eligible:
        msg = 'no portfolio transaction timestamps are available at or before now'
        raise ValueError(msg)
    return max(eligible)


def _portfolio_directories(
    data_directory: Path, portfolio_id: str | None
) -> tuple[Path, ...]:
    """Discover portfolio resource directories and apply an optional scope."""
    portfolio_root = data_directory / 'portfolio'
    if not portfolio_root.is_dir():
        msg = f'{portfolio_root}: portfolio resource directory does not exist'
        raise ValueError(msg)
    directories = tuple(
        directory
        for directory in sorted(portfolio_root.iterdir())
        if directory.is_dir() and any(directory.glob('*.yaml'))
    )
    if portfolio_id is not None and not any(
        directory.name == portfolio_id for directory in directories
    ):
        msg = f"portfolio '{portfolio_id}' does not exist"
        raise ValueError(msg)
    if not directories:
        msg = f'{portfolio_root}: no portfolios found'
        raise ValueError(msg)
    if portfolio_id is not None:
        directories = tuple(
            directory for directory in directories if directory.name == portfolio_id
        )
    return directories


class AsOfScreen(ModalScreen[datetime | None]):
    """Modal transaction-timestamp selector."""

    BINDINGS: ClassVar[list[BindingType]] = [('escape', 'dismiss(None)', 'Cancel')]
    CSS = """
    AsOfScreen { align: center middle; }
    #as-of-dialog { width: 70; height: 70%; padding: 1 2; border: heavy $accent; background: $surface; }
    #as-of-options { height: 1fr; }
    """

    def __init__(
        self, timestamps: tuple[datetime, ...], current_timestamp: datetime
    ) -> None:
        """Create a selector highlighting the current transaction timestamp."""
        super().__init__()
        self.timestamps = timestamps
        self.current_timestamp = current_timestamp

    def compose(self) -> ComposeResult:
        """Compose the timestamp selection dialog."""
        with Vertical(id='as-of-dialog'):
            yield Label('Select portfolio timestamp', classes='section-title')
            yield OptionList(
                *(timestamp.isoformat() for timestamp in self.timestamps),
                id='as-of-options',
            )

    def on_mount(self) -> None:
        """Highlight the timestamp used by the current portfolio valuation."""
        options = self.query_one('#as-of-options', OptionList)
        try:
            options.highlighted = self.timestamps.index(self.current_timestamp)
        except ValueError:
            options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the selected timestamp to the TUI portfolio browser."""
        self.dismiss(self.timestamps[event.option_index])


class PortfolioBrowserApp(App[None]):
    """TUI portfolio browser with list and full-window detail views."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ('a', 'select_as_of', 'As of'),
        ('backspace', 'return_to_list', 'All portfolios'),
        ('q', 'quit', 'Quit'),
    ]
    CSS = """
    #list-screen, #detail-screen { height: 1fr; padding: 1 2; }
    #portfolios { height: 2fr; }
    #list-summary { height: 1fr; padding: 1 2; border-top: solid $accent; }
    #identity { height: auto; margin-bottom: 1; }
    #warnings { height: auto; color: $warning; margin-bottom: 1; }
    #positions { height: 1fr; }
    #total { height: auto; margin-top: 1; padding-top: 1; border-top: solid $accent; text-style: bold; }
    .section-title { text-style: bold; margin-bottom: 1; }
    """

    def __init__(
        self,
        snapshots: tuple[PortfolioSnapshot, ...],
        initial_portfolio_id: str | None = None,
        snapshot_loader: Callable[[str | None, datetime], tuple[PortfolioSnapshot, ...]]
        | None = None,
        timestamp_loader: Callable[[str | None], tuple[datetime, ...]] | None = None,
    ) -> None:
        """Create a TUI portfolio browser and optionally open one portfolio."""
        super().__init__()
        if not snapshots:
            msg = 'at least one portfolio snapshot is required'
            raise ValueError(msg)
        self.snapshots = snapshots
        self.initial_portfolio_id = initial_portfolio_id
        self.scope_portfolio_id = initial_portfolio_id
        self.snapshot_loader = snapshot_loader
        self.timestamp_loader = timestamp_loader
        self.selected_index = 0

    def compose(self) -> ComposeResult:
        """Compose list and detail views; only one is visible at a time."""
        yield Header()
        with Vertical(id='list-screen'):
            yield DataTable(id='portfolios', cursor_type='row')
            yield Static(id='list-summary')
        with Vertical(id='detail-screen'):
            yield Static(id='identity')
            yield Static(id='warnings')
            yield Label('Positions', classes='section-title')
            yield DataTable(id='positions', cursor_type='none')
            yield Static(id='total')
        yield Footer()

    def on_mount(self) -> None:
        """Populate both views and show the requested initial screen."""
        portfolios = self.query_one('#portfolios', DataTable)
        portfolios.add_columns('Portfolio', 'Account', 'Value')
        self._populate_portfolios()
        self.query_one('#positions', DataTable).add_columns(
            'Ticker', 'Quantity', 'Value'
        )
        portfolios.focus()
        if self.initial_portfolio_id is None:
            self._show_list()
        else:
            self.selected_index = next(
                index
                for index, snapshot in enumerate(self.snapshots)
                if snapshot.portfolio_id == self.initial_portfolio_id
            )
            self._show_detail(self.selected_index)

    def _populate_portfolios(self) -> None:
        """Replace list rows with the currently loaded snapshots."""
        portfolios = self.query_one('#portfolios', DataTable)
        portfolios.clear()
        for index, snapshot in enumerate(self.snapshots):
            portfolios.add_row(
                snapshot.display_name,
                snapshot.account_id,
                self._money(snapshot.total_value, snapshot.base_currency),
                key=str(index),
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refresh the list summary when navigation changes the active row."""
        if event.data_table.id != 'portfolios' or event.row_key.value is None:
            return
        self.selected_index = int(event.row_key.value)
        self._show_list_summary(self.snapshots[self.selected_index])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open the highlighted portfolio when Enter selects its row."""
        if event.data_table.id == 'portfolios' and event.row_key.value is not None:
            self._open_portfolio(int(event.row_key.value))

    def _open_portfolio(self, index: int) -> None:
        """Open one portfolio at a timestamp offered by its as-of selector."""
        snapshot = self.snapshots[index]
        if self.timestamp_loader is None or self.snapshot_loader is None:
            self._show_detail(index)
            return
        try:
            timestamps = self.timestamp_loader(snapshot.portfolio_id)
            as_of = latest_portfolio_timestamp(timestamps, snapshot.as_of)
        except (OSError, TypeError, ValueError) as error:
            self.notify(str(error), title='Unable to load portfolio', severity='error')
            return
        if not self._reload_snapshots(
            snapshot.portfolio_id, as_of, snapshot.portfolio_id
        ):
            return
        self.scope_portfolio_id = snapshot.portfolio_id
        self._show_detail(self.selected_index)

    def action_return_to_list(self) -> None:
        """Return from full-window details to the portfolio list."""
        if self.query_one('#detail-screen').display:
            selected_portfolio_id = self.snapshots[self.selected_index].portfolio_id
            if self.scope_portfolio_id is not None and not self._reload_snapshots(
                None, self.snapshots[self.selected_index].as_of, selected_portfolio_id
            ):
                return
            self.scope_portfolio_id = None
            self._show_list()

    def action_select_as_of(self) -> None:
        """Offer transaction timestamps for the selected portfolio."""
        if self.timestamp_loader is None or self.snapshot_loader is None:
            self.notify('Timestamp selection is unavailable', severity='warning')
            return
        selected_portfolio_id = self.snapshots[self.selected_index].portfolio_id
        try:
            timestamps = self.timestamp_loader(selected_portfolio_id)
        except (OSError, TypeError, ValueError) as error:
            self.notify(str(error), title='Unable to load timestamps', severity='error')
            return
        if not timestamps:
            self.notify('No portfolio transaction timestamps', severity='warning')
            return
        current_timestamp = self.snapshots[self.selected_index].as_of
        self.push_screen(AsOfScreen(timestamps, current_timestamp), self._apply_as_of)

    def _apply_as_of(self, as_of: datetime | None) -> None:
        """Revalue the current scope at a selected transaction timestamp."""
        if as_of is None:
            return
        selected_portfolio_id = self.snapshots[self.selected_index].portfolio_id
        if self._reload_snapshots(
            self.scope_portfolio_id, as_of, selected_portfolio_id
        ):
            if self.query_one('#detail-screen').display:
                self._show_detail(self.selected_index)
            else:
                self._show_list()

    def _reload_snapshots(
        self,
        portfolio_id: str | None,
        as_of: datetime,
        selected_portfolio_id: str,
    ) -> bool:
        """Reload a scope transactionally while preserving the current selection."""
        if self.snapshot_loader is None:
            self.notify('Portfolio reloading is unavailable', severity='warning')
            return False
        try:
            snapshots = self.snapshot_loader(portfolio_id, as_of)
        except (OSError, TypeError, ValueError) as error:
            self.notify(str(error), title='Unable to load portfolios', severity='error')
            return False
        selected_index = next(
            (
                index
                for index, snapshot in enumerate(snapshots)
                if snapshot.portfolio_id == selected_portfolio_id
            ),
            None,
        )
        if selected_index is None:
            self.notify(
                f"portfolio '{selected_portfolio_id}' is no longer available",
                title='Unable to load portfolios',
                severity='error',
            )
            return False
        self.snapshots = snapshots
        self.selected_index = selected_index
        self._populate_portfolios()
        self.query_one('#portfolios', DataTable).move_cursor(row=self.selected_index)
        self.call_after_refresh(
            self._restore_portfolio_selection, selected_portfolio_id
        )
        return True

    def _restore_portfolio_selection(self, portfolio_id: str) -> None:
        """Restore selection after queued table events from a row rebuild."""
        self.selected_index = next(
            index
            for index, snapshot in enumerate(self.snapshots)
            if snapshot.portfolio_id == portfolio_id
        )
        self.query_one('#portfolios', DataTable).move_cursor(row=self.selected_index)
        if self.query_one('#list-screen').display:
            self._show_list_summary(self.snapshots[self.selected_index])

    def _show_list(self) -> None:
        """Display the portfolio list and restore its window title."""
        self.query_one('#list-screen').display = True
        self.query_one('#detail-screen').display = False
        self.title = f'Portfolio Browser as of {self.snapshots[0].as_of.isoformat()}'
        self._show_list_summary(self.snapshots[self.selected_index])
        self.query_one('#portfolios', DataTable).focus()

    def _show_list_summary(self, snapshot: PortfolioSnapshot) -> None:
        """Display compact details below the portfolio list."""
        content = self._identity(snapshot)
        if snapshot.warnings:
            content += '\nWarnings: ' + '; '.join(snapshot.warnings)
        self.query_one('#list-summary', Static).update(content)

    def _show_detail(self, index: int) -> None:
        """Display one portfolio using the full content area."""
        self.selected_index = index
        snapshot = self.snapshots[index]
        self.query_one('#list-screen').display = False
        self.query_one('#detail-screen').display = True
        self.title = f'{snapshot.portfolio_id} as of {snapshot.as_of.isoformat()}'
        self.query_one('#identity', Static).update(self._account_identity(snapshot))
        self.query_one('#warnings', Static).update('\n'.join(snapshot.warnings))
        positions = self.query_one('#positions', DataTable)
        positions.clear()
        for position in snapshot.positions:
            positions.add_row(
                position.ticker,
                format(position.quantity, 'f'),
                self._money(position.value, snapshot.base_currency),
            )
        if not snapshot.positions:
            positions.add_row('No positions', '', '')
        positions.add_row('', '', '')
        positions.add_row(
            'Cash', '', self._money(snapshot.cash, snapshot.base_currency)
        )
        self.query_one('#total', Static).update(
            f'Total value: {self._money(snapshot.total_value, snapshot.base_currency)}'
        )

    @staticmethod
    def _account_identity(snapshot: PortfolioSnapshot) -> str:
        """Format portfolio identity without duplicating detailed values."""
        return '\n'.join(
            (
                f'Name: {snapshot.display_name}',
                f'ID: {snapshot.portfolio_id}',
                f'Broker: {snapshot.broker}',
                f'Account: {snapshot.account_id}',
                f'Base currency: {snapshot.base_currency}',
                f'Ledger facts: {snapshot.transaction_count}',
            )
        )

    @staticmethod
    def _identity(snapshot: PortfolioSnapshot) -> str:
        """Format portfolio identity and valuation details."""
        return '\n'.join(
            (
                f'Name: {snapshot.display_name}',
                f'ID: {snapshot.portfolio_id}',
                f'Broker: {snapshot.broker}',
                f'Account: {snapshot.account_id}',
                f'Base currency: {snapshot.base_currency}',
                f'Cash: {PortfolioBrowserApp._money(snapshot.cash, snapshot.base_currency)}',
                f'Positions value: {PortfolioBrowserApp._money(snapshot.positions_value, snapshot.base_currency)}',
                f'Total value: {PortfolioBrowserApp._money(snapshot.total_value, snapshot.base_currency)}',
                f'Ledger facts: {snapshot.transaction_count}',
            )
        )

    @staticmethod
    def _money(value: Decimal, currency: str) -> str:
        """Format a monetary amount for terminal display."""
        return f'{value:,.2f} {currency}'


def browse_portfolios(
    data_directory: Path,
    portfolio_id: str | None,
    as_of: datetime | None,
) -> None:
    """Load portfolio valuations and run the TUI portfolio browser."""
    if as_of is None:
        timestamps = load_portfolio_timestamps(data_directory, portfolio_id)
        as_of = latest_portfolio_timestamp(timestamps, datetime.now(UTC))
    snapshots = load_portfolio_snapshots(data_directory, portfolio_id, as_of)

    def snapshot_loader(
        selected_portfolio_id: str | None, selected_as_of: datetime
    ) -> tuple[PortfolioSnapshot, ...]:
        """Load portfolio values for an interactive scope and timestamp."""
        return load_portfolio_snapshots(
            data_directory, selected_portfolio_id, selected_as_of
        )

    def timestamp_loader(
        selected_portfolio_id: str | None,
    ) -> tuple[datetime, ...]:
        """Load transaction timestamps for an interactive portfolio scope."""
        return load_portfolio_timestamps(data_directory, selected_portfolio_id)

    PortfolioBrowserApp(
        snapshots, portfolio_id, snapshot_loader, timestamp_loader
    ).run()
