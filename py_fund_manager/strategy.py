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
    find_manifest,
    find_manifest_in,
    load_directory_manifests,
    load_manifest,
    load_strategy,
)
from py_fund_manager.schemas import (
    ObjectMetadata,
    Strategy,
    StrategyAssignment,
    StrategyHistory,
    StrategyHistorySpec,
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
        stored = load_strategy(revision_path)
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
    strategy_directory = data_directory / 'strategy' / reference.name
    revision_path = _revision_path(strategy_directory, reference.revision)
    strategy = load_strategy(revision_path)
    if strategy.metadata.name != reference.name:
        msg = (
            f'{revision_path}: expected Strategy name {reference.name}, '
            f'got {strategy.metadata.name}'
        )
        raise TypeError(msg)
    if strategy_revision(strategy) != reference.revision:
        msg = f'{revision_path}: strategy content does not match its revision'
        raise TypeError(msg)
    return strategy


def load_strategy_history(path: Path) -> StrategyHistory:
    """Load and validate an effective-dated strategy history."""
    manifest = load_manifest(path)
    if not isinstance(manifest, StrategyHistory):
        msg = f'{path}: expected kind StrategyHistory, got {manifest.kind}'
        raise TypeError(msg)
    return manifest


def effective_assignment(
    history: StrategyHistory, effective_at: datetime
) -> StrategyAssignment:
    """Return the last strategy assignment effective at the requested time."""
    if effective_at.tzinfo is None:
        msg = 'effective time must include a UTC offset'
        raise ValueError(msg)
    eligible = [
        assignment
        for assignment in history.spec.assignments
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
    portfolio_directory = data_directory / 'portfolio' / portfolio_id
    if not portfolio_directory.is_dir():
        msg = f"portfolio '{portfolio_id}' does not exist"
        raise ValueError(msg)
    portfolio_manifests = load_directory_manifests(portfolio_directory)
    find_manifest_in(
        portfolio_directory,
        portfolio_manifests,
        'Portfolio',
        expected_name=portfolio_id,
    )
    strategy_directory = data_directory / 'strategy' / strategy_id
    _, strategy = find_manifest(
        strategy_directory, 'Strategy', expected_name=strategy_id
    )
    history_path = portfolio_directory / 'strategy-history.yaml'
    assignments: tuple[StrategyAssignment, ...] = ()
    history_manifests = [
        (path, manifest)
        for path, manifest in portfolio_manifests
        if manifest.kind == 'StrategyHistory'
    ]
    if len(history_manifests) > 1:
        paths = ', '.join(str(path) for path, _ in history_manifests)
        msg = (
            f'{portfolio_directory}: multiple StrategyHistory manifests found: {paths}'
        )
        raise ValueError(msg)
    if history_manifests:
        history_path, history_manifest = history_manifests[0]
        if history_manifest.metadata.name != portfolio_id:
            msg = (
                f'{history_path}: expected metadata.name {portfolio_id!r}, '
                f'got {history_manifest.metadata.name!r}'
            )
            raise ValueError(msg)
        assignments = history_manifest.spec.assignments
        for existing in assignments:
            load_strategy_revision(data_directory, existing.strategy)
    if effective_at.tzinfo is None:
        msg = 'effective_at must include a UTC offset'
        raise ValueError(msg)
    if assignments and effective_at <= assignments[-1].effective_at:
        previous = assignments[-1].effective_at.isoformat()
        requested = effective_at.isoformat()
        msg = (
            f'strategy assignment effective time {requested} must be later than '
            f'the latest assignment at {previous}'
        )
        raise ValueError(msg)
    revision = snapshot_strategy(strategy_directory, strategy)
    assignment = StrategyAssignment(
        id=f'assignment-{uuid4()}',
        effective_at=effective_at,
        strategy=StrategyRevisionReference(name=strategy_id, revision=revision),
        reason=reason,
    )
    history = StrategyHistory(
        apiVersion='v1',
        kind='StrategyHistory',
        metadata=ObjectMetadata(name=portfolio_id),
        spec=StrategyHistorySpec(assignments=(*assignments, assignment)),
    )
    document = history.model_dump(mode='json', by_alias=True, exclude_none=True)
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
