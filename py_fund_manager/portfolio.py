"""Persistence operations for portfolios, transactions, and strategies."""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, overload

import yaml

from py_fund_manager.schemas import (
    PORTFOLIO_ID_PATTERN,
    DisplayMetadata,
    Portfolio,
    PortfolioSpec,
    Strategy,
    StrategyHistory,
    Transaction,
    TransactionType,
)

type Manifest = Portfolio | Strategy | StrategyHistory
type ManifestKind = Literal['Portfolio', 'Strategy', 'StrategyHistory']


class StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: StrictSafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,  # noqa: FBT002 - PyYAML fixes this callback signature.
) -> dict[Any, Any]:
    """Construct a YAML mapping while rejecting repeated keys."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            context = 'while constructing a mapping'
            problem = 'found an unhashable key'
            raise yaml.constructor.ConstructorError(
                context,
                node.start_mark,
                problem,
                key_node.start_mark,
            ) from error
        if duplicate:
            context = 'while constructing a mapping'
            problem = f'found duplicate key {key!r}'
            raise yaml.constructor.ConstructorError(
                context,
                node.start_mark,
                problem,
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)

TRANSACTION_FIELDS = (
    'id',
    'occurred_at',
    'type',
    'ticker',
    'quantity',
    'price',
    'amount',
    'cost_basis',
    'currency',
    'fees',
    'external_id',
)


def create_portfolio(data_directory: Path, portfolio_id: str) -> Path:
    """Create a portfolio directory and its initial YAML configuration."""
    if not PORTFOLIO_ID_PATTERN.fullmatch(portfolio_id):
        msg = 'portfolio ID must use lowercase kebab-case'
        raise TypeError(msg)
    portfolio_directory = data_directory / 'portfolio' / portfolio_id
    portfolio_directory.mkdir(parents=True, exist_ok=False)
    broker = portfolio_id.partition('-')[0]
    portfolio = Portfolio(
        apiVersion='v1',
        kind='Portfolio',
        metadata=DisplayMetadata(name=portfolio_id, display_name=portfolio_id),
        spec=PortfolioSpec(
            broker=broker,
            account_id=portfolio_id,
            base_currency='USD',
        ),
    )
    document = portfolio.model_dump(mode='json', by_alias=True, exclude_none=True)
    _atomic_write_text(
        portfolio_directory / 'portfolio.yaml',
        yaml.safe_dump(document, sort_keys=False),
    )
    return portfolio_directory


def import_opening_positions(
    portfolio_directory: Path,
    source: Path,
    *,
    occurred_at: datetime | None = None,
) -> int:
    """Import canonical broker holdings as an opening transaction ledger."""
    if not source.is_file():
        msg = f"holdings file '{source}' does not exist or is not a file"
        raise TypeError(msg)
    ledger_path = portfolio_directory / 'transactions.csv'
    if ledger_path.exists():
        msg = f'{ledger_path} already exists; opening positions cannot be replaced'
        raise FileExistsError(msg)

    import_time = occurred_at or datetime.now(UTC)
    if import_time.tzinfo is None:
        msg = 'opening-position time must include a UTC offset'
        raise TypeError(msg)
    rows = _read_opening_positions(source, import_time)

    imports_directory = portfolio_directory / 'imports'
    imports_directory.mkdir(exist_ok=True)
    preserved_source = imports_directory / source.name
    if preserved_source.exists():
        msg = f'{preserved_source} already exists; source import was not replaced'
        raise FileExistsError(msg)
    _atomic_copy(source, preserved_source)
    try:
        _atomic_write_csv(ledger_path, rows)
    except Exception:
        preserved_source.unlink()
        raise
    return len(rows)


def load_portfolio(path: Path) -> Portfolio:
    """Load and validate portfolio configuration from a YAML file."""
    manifest = load_manifest(path)
    if not isinstance(manifest, Portfolio):
        msg = f'{path}: expected kind Portfolio, got {manifest.kind}'
        raise TypeError(msg)
    return manifest


def load_strategy(path: Path) -> Strategy:
    """Load and validate target allocation from a YAML file."""
    manifest = load_manifest(path)
    if not isinstance(manifest, Strategy):
        msg = f'{path}: expected kind Strategy, got {manifest.kind}'
        raise TypeError(msg)
    return manifest


def load_manifest(path: Path) -> Manifest:
    """Load one strictly parsed manifest and dispatch it by API version and kind."""
    document = _load_yaml_mapping(path)
    api_version = document.get('apiVersion')
    if api_version != 'v1':
        msg = f'{path}: unsupported apiVersion {api_version!r}'
        raise ValueError(msg)
    kind = document.get('kind')
    if kind == 'Portfolio':
        return Portfolio.model_validate(document)
    if kind == 'Strategy':
        return Strategy.model_validate(document)
    if kind == 'StrategyHistory':
        return StrategyHistory.model_validate(document)
    msg = f'{path}: unsupported kind {kind!r}'
    raise ValueError(msg)


@overload
def find_manifest(
    directory: Path,
    expected_kind: Literal['Portfolio'],
    *,
    expected_name: str | None = None,
) -> tuple[Path, Portfolio]: ...


@overload
def find_manifest(
    directory: Path,
    expected_kind: Literal['Strategy'],
    *,
    expected_name: str | None = None,
) -> tuple[Path, Strategy]: ...


@overload
def find_manifest(
    directory: Path,
    expected_kind: Literal['StrategyHistory'],
    *,
    expected_name: str | None = None,
) -> tuple[Path, StrategyHistory]: ...


def find_manifest(
    directory: Path, expected_kind: ManifestKind, *, expected_name: str | None = None
) -> tuple[Path, Manifest]:
    """Find exactly one top-level manifest of a requested kind in a directory."""
    return find_manifest_in(
        directory,
        load_directory_manifests(directory),
        expected_kind,
        expected_name=expected_name,
    )


@overload
def find_manifest_in(
    directory: Path,
    manifests: list[tuple[Path, Manifest]],
    expected_kind: Literal['Portfolio'],
    *,
    expected_name: str | None = None,
) -> tuple[Path, Portfolio]: ...


@overload
def find_manifest_in(
    directory: Path,
    manifests: list[tuple[Path, Manifest]],
    expected_kind: Literal['Strategy'],
    *,
    expected_name: str | None = None,
) -> tuple[Path, Strategy]: ...


@overload
def find_manifest_in(
    directory: Path,
    manifests: list[tuple[Path, Manifest]],
    expected_kind: Literal['StrategyHistory'],
    *,
    expected_name: str | None = None,
) -> tuple[Path, StrategyHistory]: ...


def find_manifest_in(
    directory: Path,
    manifests: list[tuple[Path, Manifest]],
    expected_kind: ManifestKind,
    *,
    expected_name: str | None = None,
) -> tuple[Path, Manifest]:
    """Find one requested kind among manifests already loaded from a directory."""
    matches = [
        (path, manifest)
        for path, manifest in manifests
        if manifest.kind == expected_kind
    ]
    if not matches:
        msg = f'{directory}: no {expected_kind} manifest found'
        raise ValueError(msg)
    if len(matches) > 1:
        paths = ', '.join(str(path) for path, _ in matches)
        msg = f'{directory}: multiple {expected_kind} manifests found: {paths}'
        raise ValueError(msg)
    path, manifest = matches[0]
    if expected_name is not None and manifest.metadata.name != expected_name:
        msg = (
            f'{path}: expected metadata.name {expected_name!r}, '
            f'got {manifest.metadata.name!r}'
        )
        raise ValueError(msg)
    return path, manifest


def load_directory_manifests(directory: Path) -> list[tuple[Path, Manifest]]:
    """Load every top-level YAML manifest in deterministic filename order."""
    return [
        (candidate, load_manifest(candidate))
        for candidate in sorted(directory.glob('*.yaml'))
    ]


def load_transactions(path: Path) -> list[Transaction]:
    """Load and validate an ordered transaction ledger from CSV."""
    transactions: list[Transaction] = []
    seen_ids: set[str] = set()
    with path.open(newline='', encoding='utf-8') as ledger_file:
        for line_number, row in enumerate(csv.DictReader(ledger_file), start=2):
            context = Path(f'{path}:{line_number}')
            normalized = {key: value for key, value in row.items() if value != ''}
            try:
                transaction = Transaction.model_validate(normalized)
            except ValueError as error:
                raise ValueError(f'{context}: {error}') from error
            if transaction.id in seen_ids:
                msg = f'{context}: duplicate transaction id {transaction.id}'
                raise ValueError(msg)
            seen_ids.add(transaction.id)
            transactions.append(transaction)
    return transactions


def _read_opening_positions(
    source: Path, occurred_at: datetime
) -> list[dict[str, str]]:
    """Validate canonical holdings CSV rows and map them to ledger rows."""
    rows: list[dict[str, str]] = []
    seen_tickers: set[str] = set()
    with source.open(newline='', encoding='utf-8-sig') as source_file:
        reader = csv.DictReader(source_file)
        required_fields = {'ticker', 'quantity'}
        if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
            msg = f'{source}: required CSV columns are ticker and quantity'
            raise ValueError(msg)
        for line_number, source_row in enumerate(reader, start=2):
            context = Path(f'{source}:{line_number}')
            transaction = Transaction.model_validate(
                {
                    'id': f'opening-{line_number - 1:06d}',
                    'occurred_at': occurred_at,
                    'type': TransactionType.OPENING_POSITION,
                    'ticker': source_row.get('ticker'),
                    'quantity': source_row.get('quantity'),
                    'cost_basis': source_row.get('cost_basis') or None,
                    'currency': source_row.get('currency') or 'USD',
                    'external_id': source_row.get('external_id')
                    or f'{source.name}:{line_number}',
                }
            )
            if transaction.ticker in seen_tickers:
                msg = f'{context}: duplicate opening position for {transaction.ticker}'
                raise ValueError(msg)
            if transaction.ticker is None:
                msg = f'{context}: opening position requires ticker'
                raise ValueError(msg)
            seen_tickers.add(transaction.ticker)
            rows.append(
                {
                    'id': transaction.id,
                    'occurred_at': transaction.occurred_at.isoformat(),
                    'type': transaction.type,
                    'ticker': transaction.ticker,
                    'quantity': str(transaction.quantity),
                    'price': '',
                    'amount': '',
                    'cost_basis': (
                        ''
                        if transaction.cost_basis is None
                        else str(transaction.cost_basis)
                    ),
                    'currency': transaction.currency,
                    'fees': '',
                    'external_id': transaction.external_id or '',
                }
            )
    if not rows:
        msg = f'{source}: holdings CSV contains no positions'
        raise ValueError(msg)
    return rows


def _atomic_write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write ledger rows atomically without replacing an existing ledger."""
    with tempfile.NamedTemporaryFile(
        mode='w',
        newline='',
        encoding='utf-8',
        dir=path.parent,
        prefix=f'.{path.name}.',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        writer = csv.DictWriter(temporary_file, fieldnames=TRANSACTION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically within its destination directory."""
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
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy a source artifact atomically into the imports directory."""
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f'.{destination.name}.',
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        shutil.copy2(source, temporary_path)
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read one YAML document whose root must be a mapping."""
    try:
        with path.open(encoding='utf-8') as source:
            document = yaml.load(
                source,
                Loader=StrictSafeLoader,  # noqa: S506 - subclasses SafeLoader only.
            )
    except yaml.YAMLError as error:
        msg = f'{path}: {error}'
        raise ValueError(msg) from error
    if not isinstance(document, dict):
        msg = f'{path}: document must be a mapping'
        raise TypeError(msg)
    return cast('dict[str, Any]', document)
