import logging

# import logging.config
import sys
from typing import Any


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


log_levels = {
    'markdown_it': logging.WARNING,
    # 'py_fund_manager': logging.INFO,
    'yfinance': logging.WARNING,
}


def setup_logging(
    logger_name: str | None,
    level: int = logging.NOTSET,
) -> logging.Logger:
    """
    Setup the logger `logger_name`
    """
    logging.basicConfig(
        stream=sys.stderr,
        datefmt='%H:%M:%S',
        format='{asctime} {name} {levelname} {message}',
        style='{',
        level=level,
    )
    logger = logging.getLogger(logger_name)
    if level != logging.NOTSET:
        logger.setLevel(level)
    # logger.propagate = False
    assert logger is not None
    # print('setup_logging() =>', logger)
    # print_logging_tree()

    for lname, llevel in log_levels.items():
        logging.getLogger(lname).setLevel(llevel)

    return logger
