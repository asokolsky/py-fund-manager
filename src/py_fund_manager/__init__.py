import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .log import setup_logging

DISTRIBUTION_NAME = 'py-fund-manager'

try:
    __version__ = version(DISTRIBUTION_NAME)
except PackageNotFoundError:
    pyproject_path = Path(__file__).parents[2] / 'pyproject.toml'
    with pyproject_path.open('rb') as pyproject_file:
        __version__ = str(tomllib.load(pyproject_file)['project']['version'])


__all__ = [
    'setup_logging',
]
