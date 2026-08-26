"""Strategy revision and effective assignment persistence operations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime  # noqa: TC003 - required by runtime type introspection.
from pathlib import Path
from uuid import uuid4

import yaml

from py_fund_manager.portfolio import (
    _atomic_write_text,
    _load_yaml_mapping,
    load_portfolio,
)
from py_fund_manager.schemas import (
    Strategy,
    StrategyAssignment,
    StrategyHistory,
    StrategyRevisionReference,
)


def canonical_strategy(strategy: Strategy) -> bytes:
    """Serialize validated strategy content in its canonical digest form."""
    document = strategy.model_dump(mode='json', by_alias=True, exclude_none=False)
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode()


def strategy_revision(strategy: Strategy) -> str:
    """Return the content revision for a validated strategy."""
    return f'sha256:{hashlib.sha256(canonical_strategy(strategy)).hexdigest()}'


def snapshot_strategy(strategy_directory: Path, strategy: Strategy) -> str:
    """Persist and verify an immutable content-addressed strategy revision."""
    revision = strategy_revision(strategy)
    revision_path = _revision_path(strategy_directory, revision)
    if revision_path.exists():
        stored = Strategy.model_validate(_load_yaml_mapping(revision_path))
        if strategy_revision(stored) != revision:
            msg = f'{revision_path}: strategy revision does not match its filename'
            raise ValueError(msg)
        return revision

    revision_path.parent.mkdir(exist_ok=True)
    document = strategy.model_dump(mode='json', by_alias=True, exclude_none=False)
    _atomic_write_text_exclusive(
        revision_path, yaml.safe_dump(document, sort_keys=False)
    )
    return revision


def load_strategy_revision(
    data_directory: Path, reference: StrategyRevisionReference
) -> Strategy:
    """Load a strategy revision and verify its identity and content digest."""
    strategy_directory = data_directory / 'strategies' / reference.id
    revision_path = _revision_path(strategy_directory, reference.revision)
    strategy = Strategy.model_validate(_load_yaml_mapping(revision_path))
    if strategy.id != reference.id:
        msg = f'{revision_path}: expected strategy ID {reference.id}, got {strategy.id}'
        raise ValueError(msg)
    if strategy_revision(strategy) != reference.revision:
        msg = f'{revision_path}: strategy content does not match its revision'
        raise ValueError(msg)
    return strategy


def load_strategy_history(path: Path) -> StrategyHistory:
    """Load and validate an effective-dated strategy history."""
    return StrategyHistory.model_validate(_load_yaml_mapping(path))


def effective_assignment(
    history: StrategyHistory, effective_at: datetime
) -> StrategyAssignment:
    """Return the last strategy assignment effective at the requested time."""
    if effective_at.tzinfo is None:
        msg = 'effective time must include a UTC offset'
        raise ValueError(msg)
    eligible = [
        assignment
        for assignment in history.assignments
        if assignment.effective_at <= effective_at
    ]
    if not eligible:
        msg = f'no strategy assignment is effective at {effective_at.isoformat()}'
        raise ValueError(msg)
    return eligible[-1]


def assign_strategy(
    data_directory: Path,
    portfolio_id: str,
    strategy_id: str,
    effective_at: datetime,
    reason: str | None = None,
) -> StrategyAssignment:
    """Snapshot a strategy and append its assignment to a portfolio history."""
    portfolio_directory = data_directory / 'portfolios' / portfolio_id
    portfolio_path = portfolio_directory / 'portfolio.yaml'
    if not portfolio_path.is_file():
        msg = f"portfolio '{portfolio_id}' does not exist"
        raise ValueError(msg)
    portfolio = load_portfolio(portfolio_path)
    if portfolio.id != portfolio_id:
        msg = f'{portfolio_path}: expected portfolio ID {portfolio_id}, got {portfolio.id}'
        raise ValueError(msg)
    strategy_directory = data_directory / 'strategies' / strategy_id
    strategy_path = strategy_directory / 'strategy.yaml'
    strategy = Strategy.model_validate(_load_yaml_mapping(strategy_path))
    if strategy.id != strategy_id:
        msg = f'{strategy_path}: expected strategy ID {strategy_id}, got {strategy.id}'
        raise ValueError(msg)
    revision = snapshot_strategy(strategy_directory, strategy)
    assignment = StrategyAssignment(
        id=f'assignment-{uuid4()}',
        effective_at=effective_at,
        strategy=StrategyRevisionReference(id=strategy_id, revision=revision),
        reason=reason,
    )
    history_path = portfolio_directory / 'strategy-history.yaml'
    assignments: tuple[StrategyAssignment, ...] = ()
    if history_path.exists():
        assignments = load_strategy_history(history_path).assignments
        for existing in assignments:
            load_strategy_revision(data_directory, existing.strategy)
    history = StrategyHistory(assignments=(*assignments, assignment))
    document = history.model_dump(mode='json', exclude_none=True)
    _atomic_write_text(history_path, yaml.safe_dump(document, sort_keys=False))
    return assignment


def _revision_path(strategy_directory: Path, revision: str) -> Path:
    """Map a serialized revision identifier to its immutable snapshot path."""
    return strategy_directory / 'revisions' / f'{revision.replace(":", "-")}.yaml'


def _atomic_write_text_exclusive(path: Path, content: str) -> None:
    """Atomically create text while refusing to replace an existing file."""
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=path.parent,
        prefix=f'.{path.name}.',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write(content)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())
    try:
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
