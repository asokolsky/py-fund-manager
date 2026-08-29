"""Persistence operations for portfolios, transactions, and strategies."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, overload

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

from py_fund_manager.schemas import (
    PORTFOLIO_ID_PATTERN,
    DisplayMetadata,
    Execution,
    OrderSide,
    Portfolio,
    PortfolioSpec,
    Strategy,
    StrategyHistory,
    Transaction,
    TransactionType,
    normalize_cash_flow_amount,
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
PORTFOLIO_SCAFFOLD_FILES = frozenset(
    {'README.md', '.gitignore', '.py-fund-manager-documentation-only'}
)

ACTIVITY_SECURITY_EVENTS = {
    TransactionType.POSITION_ADJUSTMENT,
    TransactionType.BUY,
    TransactionType.SELL,
    TransactionType.TRANSFER_IN,
    TransactionType.TRANSFER_OUT,
}
ACTIVITY_CASH_EVENTS = {
    TransactionType.DEPOSIT,
    TransactionType.WITHDRAWAL,
    TransactionType.DIVIDEND,
    TransactionType.INTEREST,
    TransactionType.FEE,
}


@dataclass(frozen=True)
class ActivityImportResult:
    """Counts produced by an idempotent activity import."""

    imported: int
    skipped: int


def create_portfolio(
    data_directory: Path,
    portfolio_id: str,
    *,
    broker: str,
    account_id: str,
) -> Path:
    """Create a portfolio directory and its initial YAML configuration."""
    if not PORTFOLIO_ID_PATTERN.fullmatch(portfolio_id):
        msg = 'portfolio ID must use lowercase kebab-case'
        raise TypeError(msg)
    portfolio_directory = data_directory / 'portfolio' / portfolio_id
    portfolio_directory.mkdir(parents=True, exist_ok=True)
    blocking_entries = sorted(
        path.name
        for path in portfolio_directory.iterdir()
        if path.name not in PORTFOLIO_SCAFFOLD_FILES
    )
    if blocking_entries:
        entries = ', '.join(blocking_entries)
        msg = f'{portfolio_directory} already contains portfolio data: {entries}'
        raise FileExistsError(msg)
    portfolio = Portfolio(
        apiVersion='v1',
        kind='Portfolio',
        metadata=DisplayMetadata(name=portfolio_id, display_name=portfolio_id),
        spec=PortfolioSpec(
            broker=broker,
            account_id=account_id,
            base_currency='USD',
        ),
    )
    document = portfolio.model_dump(mode='json', by_alias=True, exclude_none=True)
    _atomic_write_text_exclusive(
        portfolio_directory / 'portfolio.yaml',
        yaml.safe_dump(document, sort_keys=False),
    )
    return portfolio_directory


def import_opening_snapshot(
    portfolio_directory: Path,
    source: Path,
    *,
    occurred_at: datetime | None = None,
) -> int:
    """Import a canonical opening position and cash snapshot."""
    if not source.is_file():
        msg = f"opening snapshot '{source}' does not exist or is not a file"
        raise TypeError(msg)
    ledger_path = portfolio_directory / 'transactions.csv'
    if ledger_path.exists():
        msg = f'{ledger_path} already exists; opening positions cannot be replaced'
        raise FileExistsError(msg)

    import_time = occurred_at or datetime.now(UTC)
    if import_time.tzinfo is None:
        msg = 'opening snapshot time must include a UTC offset'
        raise TypeError(msg)
    _, portfolio = find_manifest(
        portfolio_directory,
        'Portfolio',
        expected_name=portfolio_directory.name,
    )
    transactions = _read_opening_snapshot(
        source, import_time, portfolio.spec.base_currency
    )
    validate_transaction_ledger(transactions)
    rows = [_transaction_row(transaction) for transaction in transactions]

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
    return len(transactions)


def initialize_opening_balances(
    portfolio_directory: Path,
    balances: Mapping[str, Decimal],
    *,
    occurred_at: datetime | None = None,
) -> int:
    """Write inline opening cash and position balances to a new ledger."""
    ledger_path = portfolio_directory / 'transactions.csv'
    if ledger_path.exists():
        msg = f'{ledger_path} already exists; opening positions cannot be replaced'
        raise FileExistsError(msg)
    if not balances:
        msg = 'opening balances must contain at least one asset'
        raise ValueError(msg)

    opening_time = occurred_at or datetime.now(UTC)
    if opening_time.tzinfo is None:
        msg = 'opening balance time must include a UTC offset'
        raise TypeError(msg)
    _, portfolio = find_manifest(
        portfolio_directory,
        'Portfolio',
        expected_name=portfolio_directory.name,
    )
    transactions: list[Transaction] = []
    for index, (asset, raw_value) in enumerate(balances.items(), start=1):
        is_cash = asset == portfolio.spec.base_currency
        transaction_type = (
            TransactionType.OPENING_CASH
            if is_cash
            else TransactionType.OPENING_POSITION
        )
        value = (
            normalize_cash_flow_amount(raw_value, 'opening cash')
            if is_cash
            else raw_value
        )
        try:
            transactions.append(
                Transaction.model_validate(
                    {
                        'id': f'opening-{index:06d}',
                        'occurred_at': opening_time,
                        'type': transaction_type,
                        'ticker': None if is_cash else asset,
                        'quantity': None if is_cash else value,
                        'amount': value if is_cash else None,
                        'currency': portfolio.spec.base_currency,
                    }
                )
            )
        except ValueError as error:
            raise ValueError(f'opening balance {asset}: {error}') from error
    validate_transaction_ledger(transactions)
    _atomic_write_csv(
        ledger_path,
        [_transaction_row(transaction) for transaction in transactions],
    )
    return len(transactions)


def import_activity(portfolio_directory: Path, source: Path) -> ActivityImportResult:
    """Append CSV activity or JSON executions, skipping identical known events."""
    if not source.is_file():
        msg = f"activity file '{source}' does not exist or is not a file"
        raise TypeError(msg)
    ledger_path = portfolio_directory / 'transactions.csv'
    if not ledger_path.is_file():
        msg = f'{ledger_path} does not exist; import an opening snapshot first'
        raise FileNotFoundError(msg)
    _, portfolio = find_manifest(
        portfolio_directory,
        'Portfolio',
        expected_name=portfolio_directory.name,
    )
    existing = load_transactions(ledger_path)
    incoming = _read_import(source, portfolio.spec.base_currency)
    known = {
        transaction.external_id: transaction
        for transaction in existing
        if transaction.external_id is not None
    }
    additions: list[Transaction] = []
    skipped = 0
    for transaction in incoming:
        external_id = transaction.external_id
        if external_id is None:  # Defensive: activity parsing requires this field.
            msg = f'{source}: parsed activity is missing external_id'
            raise ValueError(msg)
        previous = known.get(external_id)
        if previous is None:
            additions.append(transaction)
            known[external_id] = transaction
            continue
        if _transaction_fact(previous) != _transaction_fact(transaction):
            msg = (
                f'{source}: external_id {external_id!r} conflicts '
                'with the existing ledger'
            )
            raise ValueError(msg)
        skipped += 1

    combined = [*existing, *additions]
    if additions and existing and additions[0].occurred_at < existing[-1].occurred_at:
        incoming_transaction = additions[0]
        previous = existing[-1]
        msg = (
            f'{source}: activity external_id {incoming_transaction.external_id!r} at '
            f'{incoming_transaction.occurred_at.isoformat()} predates the latest '
            'ledger event '
            f'{previous.external_id or previous.id!r} at '
            f'{previous.occurred_at.isoformat()}; activity imports are append-only'
        )
        raise ValueError(msg)
    validate_transaction_ledger(combined, path=source)
    imports_directory = portfolio_directory / 'imports'
    imports_directory.mkdir(exist_ok=True)
    preserved_source = imports_directory / source.name
    if preserved_source.exists():
        # An archived source with missing ledger facts indicates external ledger
        # editing; do not silently reconstruct only part of that prior import.
        if preserved_source.read_bytes() == source.read_bytes() and not additions:
            return ActivityImportResult(imported=0, skipped=skipped)
        msg = f'{preserved_source} already exists; source import was not replaced'
        raise FileExistsError(msg)
    _atomic_copy(source, preserved_source)
    try:
        _atomic_write_csv(
            ledger_path,
            [_transaction_row(transaction) for transaction in combined],
        )
    except Exception:
        preserved_source.unlink()
        raise
    return ActivityImportResult(imported=len(additions), skipped=skipped)


def execution_transaction(execution: Execution) -> Transaction:
    """Map one confirmed broker fill into an immutable ledger transaction."""
    return Transaction(
        id=f'execution-{execution.id}',
        occurred_at=execution.executed_at,
        type=(
            TransactionType.BUY
            if execution.side == OrderSide.BUY
            else TransactionType.SELL
        ),
        ticker=execution.ticker,
        quantity=execution.quantity,
        price=execution.price,
        amount=execution.quantity * execution.price,
        currency=execution.currency,
        fees=execution.fees,
        external_id=execution.id,
    )


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
    with path.open(newline='', encoding='utf-8') as ledger_file:
        for line_number, row in enumerate(csv.DictReader(ledger_file), start=2):
            context = Path(f'{path}:{line_number}')
            normalized = {key: value for key, value in row.items() if value != ''}
            try:
                transaction = Transaction.model_validate(normalized)
            except ValueError as error:
                raise ValueError(f'{context}: {error}') from error
            transactions.append(transaction)
    validate_transaction_ledger(transactions, path=path)
    return transactions


def validate_transaction_ledger(
    transactions: list[Transaction] | tuple[Transaction, ...],
    *,
    path: Path | None = None,
) -> None:
    """Require stable identities and chronological ordering across a ledger."""
    seen_ids: set[str] = set()
    seen_external_ids: set[str] = set()
    previous: Transaction | None = None
    for index, transaction in enumerate(transactions, start=2):
        context = f'{path}:{index}' if path is not None else f'transaction row {index}'
        if transaction.id in seen_ids:
            msg = f'{context}: duplicate transaction id {transaction.id}'
            raise ValueError(msg)
        seen_ids.add(transaction.id)
        if (
            transaction.external_id is not None
            and transaction.external_id in seen_external_ids
        ):
            msg = f'{context}: duplicate external_id {transaction.external_id}'
            raise ValueError(msg)
        if transaction.external_id is not None:
            seen_external_ids.add(transaction.external_id)
        if previous is not None and transaction.occurred_at < previous.occurred_at:
            msg = f'{context}: transaction {transaction.id} occurs before {previous.id}'
            raise ValueError(msg)
        previous = transaction


def _read_opening_snapshot(
    source: Path, occurred_at: datetime, base_currency: str
) -> list[Transaction]:
    """Validate canonical opening position and cash rows."""
    transactions: list[Transaction] = []
    seen_tickers: set[str] = set()
    cash_rows = 0
    with source.open(newline='', encoding='utf-8-sig') as source_file:
        reader = csv.DictReader(source_file)
        required_fields = {'asset'}
        if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
            msg = f'{source}: required CSV column is asset'
            raise ValueError(msg)
        allowed_fields = {'asset', 'quantity', 'amount', 'cost_basis'}
        unsupported_fields = set(reader.fieldnames) - allowed_fields
        if unsupported_fields:
            fields = ', '.join(sorted(unsupported_fields))
            msg = f'{source}: unsupported CSV columns: {fields}'
            raise ValueError(msg)
        for line_number, source_row in enumerate(reader, start=2):
            context = Path(f'{source}:{line_number}')
            has_quantity = bool(source_row.get('quantity'))
            has_amount = bool(source_row.get('amount'))
            if has_quantity == has_amount:
                msg = f'{context}: row requires exactly one of quantity or amount'
                raise ValueError(msg)
            asset = (source_row.get('asset') or '').strip().upper()
            is_position = has_quantity
            transaction_type = (
                TransactionType.OPENING_POSITION
                if is_position
                else TransactionType.OPENING_CASH
            )
            try:
                transaction = Transaction.model_validate(
                    {
                        'id': f'opening-{line_number - 1:06d}',
                        'occurred_at': occurred_at,
                        'type': transaction_type,
                        'ticker': asset if is_position else None,
                        'quantity': source_row.get('quantity') or None,
                        'amount': source_row.get('amount') or None,
                        'cost_basis': source_row.get('cost_basis') or None,
                        'currency': base_currency if is_position else asset,
                    }
                )
            except ValueError as error:
                raise ValueError(f'{context}: {error}') from error
            if transaction.currency != base_currency:
                msg = (
                    f'{context}: opening fact uses {transaction.currency}; '
                    f'portfolio uses {base_currency}'
                )
                raise ValueError(msg)
            if is_position:
                if transaction.ticker in seen_tickers:
                    msg = (
                        f'{context}: duplicate opening position for '
                        f'{transaction.ticker}'
                    )
                    raise ValueError(msg)
                if transaction.ticker is None:
                    msg = f'{context}: opening position requires ticker'
                    raise ValueError(msg)
                seen_tickers.add(transaction.ticker)
            else:
                cash_rows += 1
                if cash_rows > 1:
                    msg = f'{context}: opening snapshot contains multiple cash rows'
                    raise ValueError(msg)
            transactions.append(transaction)
    if not transactions:
        msg = f'{source}: opening CSV contains no facts'
        raise ValueError(msg)
    return transactions


def _read_import(source: Path, base_currency: str) -> list[Transaction]:
    """Read one supported portfolio import format."""
    suffix = source.suffix.lower()
    if suffix == '.csv':
        return _read_activity_csv(source, base_currency)
    if suffix == '.json':
        return _read_execution_json(source, base_currency)
    msg = f'{source}: portfolio imports must use a .csv or .json extension'
    raise ValueError(msg)


def _read_execution_json(source: Path, base_currency: str) -> list[Transaction]:
    """Validate canonical broker execution JSON and map it to ledger facts."""
    try:
        document = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f'{source}: invalid execution JSON: {error}') from error
    if not isinstance(document, list):
        msg = f'{source}: execution JSON must contain an array'
        raise TypeError(msg)
    if not document:
        msg = f'{source}: execution JSON contains no executions'
        raise ValueError(msg)
    transactions: list[Transaction] = []
    for index, item in enumerate(document):
        context = f'{source}: execution {index}'
        try:
            execution = Execution.model_validate(item)
        except (TypeError, ValueError) as error:
            raise ValueError(f'{context}: {error}') from error
        if execution.currency != base_currency:
            msg = (
                f'{context}: execution uses {execution.currency}; '
                f'portfolio uses {base_currency}'
            )
            raise ValueError(msg)
        transactions.append(execution_transaction(execution))
    validate_transaction_ledger(transactions, path=source)
    return transactions


def _read_activity_csv(source: Path, base_currency: str) -> list[Transaction]:
    """Validate canonical independently timestamped broker activity rows."""
    transactions: list[Transaction] = []
    seen_external_ids: set[str] = set()
    allowed_fields = {
        'occurred_at',
        'event',
        'asset',
        'quantity',
        'amount',
        'price',
        'cost_basis',
        'fees',
        'external_id',
    }
    with source.open(newline='', encoding='utf-8-sig') as source_file:
        reader = csv.DictReader(source_file)
        required_fields = {'occurred_at', 'event', 'asset', 'external_id'}
        if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
            fields = ', '.join(sorted(required_fields))
            msg = f'{source}: required CSV columns are {fields}'
            raise ValueError(msg)
        unsupported_fields = set(reader.fieldnames) - allowed_fields
        if unsupported_fields:
            fields = ', '.join(sorted(unsupported_fields))
            msg = f'{source}: unsupported CSV columns: {fields}'
            raise ValueError(msg)
        for line_number, source_row in enumerate(reader, start=2):
            context = Path(f'{source}:{line_number}')
            event_value = (source_row.get('event') or '').strip()
            try:
                event = TransactionType(event_value)
            except ValueError as error:
                msg = f'{context}: unsupported activity event {event_value!r}'
                raise ValueError(msg) from error
            if event not in ACTIVITY_SECURITY_EVENTS | ACTIVITY_CASH_EVENTS:
                msg = f'{context}: unsupported activity event {event_value!r}'
                raise ValueError(msg)
            external_id = (source_row.get('external_id') or '').strip()
            if not external_id:
                msg = f'{context}: external_id is required'
                raise ValueError(msg)
            if external_id in seen_external_ids:
                msg = f'{context}: duplicate external_id {external_id}'
                raise ValueError(msg)
            seen_external_ids.add(external_id)
            asset = (source_row.get('asset') or '').strip().upper()
            is_security = event in ACTIVITY_SECURITY_EVENTS
            if not is_security and any(
                source_row.get(field) for field in ('quantity', 'price', 'cost_basis')
            ):
                msg = (
                    f'{context}: cash activity does not accept quantity, price, '
                    'or cost_basis'
                )
                raise ValueError(msg)
            transaction_id = hashlib.sha256(external_id.encode()).hexdigest()[:20]
            try:
                transaction = Transaction.model_validate(
                    {
                        'id': f'activity-{transaction_id}',
                        'occurred_at': source_row.get('occurred_at'),
                        'type': event,
                        'ticker': asset if is_security else None,
                        'currency': base_currency if is_security else asset,
                        'quantity': source_row.get('quantity') or None,
                        'amount': source_row.get('amount') or None,
                        'price': source_row.get('price') or None,
                        'cost_basis': source_row.get('cost_basis') or None,
                        'fees': source_row.get('fees') or 0,
                        'external_id': external_id,
                    }
                )
            except ValueError as error:
                raise ValueError(f'{context}: {error}') from error
            if transaction.currency != base_currency:
                msg = (
                    f'{context}: activity uses {transaction.currency}; '
                    f'portfolio uses {base_currency}'
                )
                raise ValueError(msg)
            transactions.append(transaction)
    if not transactions:
        msg = f'{source}: activity CSV contains no events'
        raise ValueError(msg)
    validate_transaction_ledger(transactions, path=source)
    return transactions


def _transaction_fact(transaction: Transaction) -> dict[str, Any]:
    """Return transaction content excluding its local ledger identity."""
    return transaction.model_dump(mode='json', exclude={'id'})


def _transaction_row(transaction: Transaction) -> dict[str, str]:
    """Serialize one validated transaction into the canonical CSV columns."""
    document = transaction.model_dump(mode='json')
    return {
        field: '' if document[field] is None else str(document[field])
        for field in TRANSACTION_FIELDS
    }


def _atomic_write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write complete ledger rows through an atomic replacement."""
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
