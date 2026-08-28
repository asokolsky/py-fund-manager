"""Generate a direct-replication strategy from an SPY holdings workbook."""

from __future__ import annotations

import re
import sys
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from defusedxml import ElementTree

DIRECTORY = Path(__file__).resolve().parent
DEFAULT_SOURCE = DIRECTORY / 'holdings-daily-us-en-spy.xlsx'
DEFAULT_OUTPUT = DIRECTORY / 'strategy.yaml'
EXCEL_NAMESPACE = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
OUTPUT_PRECISION = Decimal('0.000000000001')


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """Read the XLSX shared-string table."""
    root = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
    return [
        ''.join(node.text or '' for node in item.findall('.//x:t', EXCEL_NAMESPACE))
        for item in root.findall('x:si', EXCEL_NAMESPACE)
    ]


def _cell_value(cell: Any, strings: list[str]) -> str:
    """Return one XLSX cell value as text."""
    value = cell.find('x:v', EXCEL_NAMESPACE)
    if value is None or value.text is None:
        return ''
    if cell.get('t') == 's':
        return str(strings[int(value.text)])
    return str(value.text)


def _workbook_rows(source: Path) -> list[dict[str, str]]:
    """Extract worksheet rows keyed by Excel column letter."""
    with zipfile.ZipFile(source) as archive:
        strings = _shared_strings(archive)
        worksheet = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))

    rows: list[dict[str, str]] = []
    for row in worksheet.findall('.//x:sheetData/x:row', EXCEL_NAMESPACE):
        values: dict[str, str] = {}
        for cell in row.findall('x:c', EXCEL_NAMESPACE):
            reference = cell.get('r', '')
            match = re.match(r'[A-Z]+', reference)
            if match:
                values[match.group()] = _cell_value(cell, strings)
        rows.append(values)
    return rows


def _snapshot_date(rows: list[dict[str, str]]) -> str:
    """Return the holdings date in ISO format."""
    value = rows[2].get('B', '')
    match = re.fullmatch(r'As of (\d{2})-([A-Za-z]{3})-(\d{4})', value)
    if not match:
        raise ValueError(f'unsupported holdings date: {value!r}')
    day, month_name, year = match.groups()
    months = {
        'Jan': '01',
        'Feb': '02',
        'Mar': '03',
        'Apr': '04',
        'May': '05',
        'Jun': '06',
        'Jul': '07',
        'Aug': '08',
        'Sep': '09',
        'Oct': '10',
        'Nov': '11',
        'Dec': '12',
    }
    return f'{year}-{months[month_name]}-{day}'


def _equity_weights(rows: list[dict[str, str]]) -> list[tuple[str, Decimal]]:
    """Read equity weights while excluding cash and contra-account rows."""
    positions: list[tuple[str, Decimal]] = []
    for row in rows:
        ticker = row.get('B', '').strip()
        weight = row.get('E', '').strip()
        name = row.get('A', '').strip().upper()
        if not ticker or not weight or ticker == '-' or name.startswith('CONTRA '):
            continue
        try:
            percentage = Decimal(weight)
        except ArithmeticError:
            continue
        positions.append((ticker.upper(), percentage))
    if not positions:
        msg = 'source contains no equity rows'
        raise ValueError(msg)
    tickers = [ticker for ticker, _ in positions]
    if len(tickers) != len(set(tickers)):
        msg = 'source contains duplicate equity tickers'
        raise ValueError(msg)
    return positions


def _normalized_weights(
    positions: list[tuple[str, Decimal]],
) -> list[tuple[str, Decimal]]:
    """Normalize percentage weights to decimal fractions totaling exactly one."""
    total = sum(weight for _, weight in positions)
    normalized = [
        (ticker, (weight / total).quantize(OUTPUT_PRECISION))
        for ticker, weight in positions
    ]
    residual = Decimal(1) - sum(weight for _, weight in normalized)
    ticker, weight = normalized[-1]
    normalized[-1] = (ticker, weight + residual)
    return normalized


def generate_strategy(source: Path, output: Path) -> None:
    """Generate validated strategy YAML from an SPY holdings workbook."""
    rows = _workbook_rows(source)
    positions = _normalized_weights(_equity_weights(rows))
    document = {
        'apiVersion': 'v1',
        'kind': 'Strategy',
        'metadata': {
            'name': 'SnP500-direct',
            'display_name': f'S&P 500 direct replication ({_snapshot_date(rows)})',
        },
        'spec': {
            'benchmark': '$SPX',
            'allocation': {
                'type': 'target_weights',
                'positions': {
                    ticker: format(weight, '.12f') for ticker, weight in positions
                },
            },
        },
    }
    output.write_text(yaml.safe_dump(document, sort_keys=False), encoding='utf-8')


def main() -> int:
    """Generate the strategy using optional source and output arguments."""
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    generate_strategy(source, output)
    print(f'Generated {output} from {source}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
