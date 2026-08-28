"""Side-effect-free validation for a complete py-fund-manager data root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from py_fund_manager.portfolio import (
    find_manifest,
    load_directory_manifests,
    load_portfolio,
    load_strategy,
    load_transactions,
)
from py_fund_manager.schemas import StrategyRevisionReference
from py_fund_manager.strategy import load_strategy_history, load_strategy_revision

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class DataValidationError(ValueError):
    """One or more independently discovered data-root validation failures."""

    def __init__(self, errors: list[str]) -> None:
        """Create an aggregate error from ordered validation messages."""
        self.errors = tuple(errors)
        super().__init__('\n'.join(errors))


@dataclass(frozen=True)
class ValidationSummary:
    """Counts of resources verified in a complete data root."""

    portfolios: int
    strategies: int
    strategy_histories: int
    revisions: int

    def message(self) -> str:
        """Return a concise human-readable validation summary."""
        return (
            f'Validated {_counted(self.portfolios, "Portfolio")}, '
            f'{_counted(self.strategies, "Strategy", "Strategies")}, '
            f'{_counted(self.strategy_histories, "StrategyHistory")}, '
            f'and {_counted(self.revisions, "revision")}.'
        )


def validate_data_root(data_directory: Path) -> ValidationSummary:
    """Validate all current manifests, ledgers, references, and revisions."""
    errors: list[str] = []
    portfolios = _validate_portfolios(data_directory, errors)
    strategies, revisions = _validate_strategies(data_directory, errors)
    if errors:
        raise DataValidationError(errors)
    return ValidationSummary(
        portfolios=portfolios[0],
        strategies=strategies,
        strategy_histories=portfolios[1],
        revisions=revisions,
    )


def _validate_portfolios(data_directory: Path, errors: list[str]) -> tuple[int, int]:
    """Validate Portfolio directories and their effective Strategy histories."""
    portfolio_root = data_directory / 'portfolio'
    portfolio_count = 0
    history_count = 0
    if not portfolio_root.is_dir():
        errors.append(f'{portfolio_root}: portfolio resource directory does not exist')
        return portfolio_count, history_count
    for directory in _resource_directories(portfolio_root):
        try:
            path, _ = find_manifest(
                directory, 'Portfolio', expected_name=directory.name
            )
            load_portfolio(path)
            portfolio_count += 1
            ledger = directory / 'transactions.csv'
            if ledger.exists():
                load_transactions(ledger)
            histories = [
                (manifest_path, manifest)
                for manifest_path, manifest in load_directory_manifests(directory)
                if manifest.kind == 'StrategyHistory'
            ]
            _require_at_most_one_history(directory, histories)
            if histories:
                history_path, history_manifest = histories[0]
                _require_expected_name(
                    history_path, history_manifest.metadata.name, directory.name
                )
                history = load_strategy_history(history_path)
                for assignment in history.spec.assignments:
                    load_strategy_revision(data_directory, assignment.strategy)
                history_count += 1
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))
    return portfolio_count, history_count


def _validate_strategies(data_directory: Path, errors: list[str]) -> tuple[int, int]:
    """Validate current Strategy resources and every immutable revision."""
    strategy_root = data_directory / 'strategy'
    strategy_count = 0
    revision_count = 0
    if not strategy_root.is_dir():
        errors.append(f'{strategy_root}: strategy resource directory does not exist')
        return strategy_count, revision_count
    for directory in _resource_directories(strategy_root):
        try:
            path, _ = find_manifest(directory, 'Strategy', expected_name=directory.name)
            load_strategy(path)
            strategy_count += 1
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))
        revisions_directory = directory / 'revisions'
        for revision_path in sorted(revisions_directory.glob('sha256-*.yaml')):
            revision = revision_path.stem.replace('sha256-', 'sha256:', 1)
            try:
                load_strategy_revision(
                    data_directory,
                    StrategyRevisionReference(name=directory.name, revision=revision),
                )
                revision_count += 1
            except (OSError, TypeError, ValueError) as error:
                errors.append(str(error))
    return strategy_count, revision_count


def _resource_directories(root: Path) -> list[Path]:
    """Return resource directories in stable identity order."""
    return sorted(path for path in root.iterdir() if path.is_dir())


def _require_at_most_one_history(
    directory: Path, histories: Sequence[tuple[Path, object]]
) -> None:
    """Reject ambiguous Portfolio directories with multiple histories."""
    if len(histories) > 1:
        paths = ', '.join(str(item[0]) for item in histories)
        msg = f'{directory}: multiple StrategyHistory manifests found: {paths}'
        raise ValueError(msg)


def _require_expected_name(path: Path, actual: str, expected: str) -> None:
    """Require a manifest identity to match its resource directory."""
    if actual != expected:
        msg = f'{path}: expected metadata.name {expected!r}, got {actual!r}'
        raise ValueError(msg)


def _counted(count: int, singular: str, plural: str | None = None) -> str:
    """Format a count with its correctly inflected resource name."""
    label = singular if count == 1 else (plural or f'{singular}s')
    return f'{count} {label}'
