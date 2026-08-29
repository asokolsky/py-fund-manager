"""Side-effect-free validation for a complete py-fund-manager data root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from py_fund_manager.portfolio import (
    Manifest,
    find_manifest_in,
    load_manifest,
    load_transactions,
)
from py_fund_manager.schemas import StrategyHistory, StrategyRevisionReference
from py_fund_manager.strategy import load_strategy_revision

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


@dataclass(frozen=True)
class PortfolioValidationCounts:
    """Counts produced while validating Portfolio resource directories."""

    portfolios: int
    strategy_histories: int
    validated_revisions: frozenset[tuple[str, str]]


def validate_data_root(data_directory: Path) -> ValidationSummary:
    """Validate all current manifests, ledgers, references, and revisions."""
    errors: list[str] = []
    portfolios = _validate_portfolios(data_directory, errors)
    strategies, revisions = _validate_strategies(
        data_directory, errors, portfolios.validated_revisions
    )
    if errors:
        raise DataValidationError(errors)
    return ValidationSummary(
        portfolios=portfolios.portfolios,
        strategies=strategies,
        strategy_histories=portfolios.strategy_histories,
        revisions=revisions,
    )


def _validate_portfolios(
    data_directory: Path, errors: list[str]
) -> PortfolioValidationCounts:
    """Validate Portfolio directories and their effective Strategy histories."""
    portfolio_root = data_directory / 'portfolio'
    portfolio_count = 0
    history_count = 0
    validated_revisions: set[tuple[str, str]] = set()
    if not portfolio_root.is_dir():
        errors.append(f'{portfolio_root}: portfolio resource directory does not exist')
        return PortfolioValidationCounts(
            portfolio_count, history_count, frozenset(validated_revisions)
        )
    for directory in _resource_directories(portfolio_root):
        manifests = _load_manifests(directory, errors)
        try:
            find_manifest_in(
                directory, manifests, 'Portfolio', expected_name=directory.name
            )
            portfolio_count += 1
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))

        ledger = directory / 'transactions.csv'
        if ledger.exists():
            try:
                load_transactions(ledger)
            except (OSError, TypeError, ValueError) as error:
                errors.append(str(error))

        histories = [
            (manifest_path, manifest)
            for manifest_path, manifest in manifests
            if isinstance(manifest, StrategyHistory)
        ]
        try:
            _require_at_most_one_history(directory, histories)
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))
            continue
        if histories:
            history_path, history_manifest = histories[0]
            try:
                _require_expected_name(
                    history_path, history_manifest.metadata.name, directory.name
                )
            except (OSError, TypeError, ValueError) as error:
                errors.append(str(error))
            for assignment in history_manifest.spec.assignments:
                try:
                    load_strategy_revision(data_directory, assignment.strategy)
                    validated_revisions.add(
                        (assignment.strategy.name, assignment.strategy.revision)
                    )
                except (OSError, TypeError, ValueError) as error:
                    errors.append(
                        f'{history_path}: assignment {assignment.id!r}: {error}'
                    )
            history_count += 1
    return PortfolioValidationCounts(
        portfolio_count, history_count, frozenset(validated_revisions)
    )


def _validate_strategies(
    data_directory: Path,
    errors: list[str],
    validated_revisions: frozenset[tuple[str, str]],
) -> tuple[int, int]:
    """Validate current Strategy resources and every immutable revision."""
    strategy_root = data_directory / 'strategy'
    strategy_count = 0
    revision_count = 0
    if not strategy_root.is_dir():
        errors.append(f'{strategy_root}: strategy resource directory does not exist')
        return strategy_count, revision_count
    for directory in _resource_directories(strategy_root):
        manifests = _load_manifests(directory, errors)
        try:
            find_manifest_in(
                directory, manifests, 'Strategy', expected_name=directory.name
            )
            strategy_count += 1
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))
        revisions_directory = directory / 'revisions'
        for revision_path in sorted(revisions_directory.glob('sha256-*.yaml')):
            revision = revision_path.stem.replace('sha256-', 'sha256:', 1)
            if (directory.name, revision) in validated_revisions:
                revision_count += 1
                continue
            try:
                load_strategy_revision(
                    data_directory,
                    StrategyRevisionReference(name=directory.name, revision=revision),
                )
                revision_count += 1
            except (OSError, TypeError, ValueError) as error:
                errors.append(str(error))
    return strategy_count, revision_count


def _load_manifests(directory: Path, errors: list[str]) -> list[tuple[Path, Manifest]]:
    """Load valid manifests once while preserving independent parse failures."""
    manifests: list[tuple[Path, Manifest]] = []
    for path in sorted(directory.glob('*.yaml')):
        try:
            manifests.append((path, load_manifest(path)))
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))
    return manifests


def _resource_directories(root: Path) -> list[Path]:
    """Return resource directories in stable identity order."""
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not _is_documentation_only_directory(path)
    )


def _is_documentation_only_directory(directory: Path) -> bool:
    """Recognize documented scenarios that do not persist a resource fixture."""
    entries = {path.name for path in directory.iterdir()}
    return 'README.md' in entries and entries <= {'README.md', '.gitignore'}


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
