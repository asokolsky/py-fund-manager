"""Dispatch broker-native activity files to their conversion adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from py_fund_manager.broker.ibkr import (
    is_activity_statement as is_ibkr_activity_statement,
)
from py_fund_manager.broker.ibkr import read_activity_transactions as read_ibkr_activity
from py_fund_manager.portfolio import ActivityImportResult, import_activity

if TYPE_CHECKING:
    from pathlib import Path


def import_broker_activity(
    portfolio_directory: Path, source: Path
) -> ActivityImportResult:
    """Import canonical activity or dispatch a recognized broker-native file."""
    reader = read_ibkr_activity if is_ibkr_activity_statement(source) else None
    return import_activity(portfolio_directory, source, reader=reader)
