"""Console logging: one colored StreamHandler, noisy libraries tamed.

`configure_logging` is called once at each CLI edge; every other module just uses `logging.getLogger(__name__)`.
The module name is safe -- `import logging` below resolves to the stdlib via absolute import.
"""

import logging

LOG_FMT = '%(asctime)s|%(levelname)s|%(name)s: %(message)s'
LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'

LEVEL_COLORS_ANSI = {
    logging.DEBUG: '\033[37m',
    logging.INFO: '\033[32m',
    logging.WARNING: '\033[33m',
    logging.ERROR: '\033[31m',
    logging.CRITICAL: '\033[91m',
}
_RESET = '\033[0m'


class _ColorFormatter(logging.Formatter):
    """Wrap each formatted line in its level's ANSI color."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record, then wrap the whole line in its level's ANSI color."""
        color = LEVEL_COLORS_ANSI.get(record.levelno, _RESET)
        return f'{color}{super().format(record)}{_RESET}'


def configure_logging(level: int | str = 'INFO') -> None:
    """Install one colored console handler on the root logger and quiet noisy libraries."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(_ColorFormatter(fmt=LOG_FMT, datefmt=LOG_DATEFMT))
    root.addHandler(handler)

    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
