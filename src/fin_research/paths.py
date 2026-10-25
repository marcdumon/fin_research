"""Where everything lives on disk.

One module so no other file ever builds a path from `__file__`. The app folder is the folder of the Python environment
(the repo in development, the install folder of an installed app): it holds `fin_research.toml` (not in git),
`screens.toml` and `.env`. The data folder is `data_dir` of `fin_research.toml`: the filings, the DuckDB store, the run
ledger and the reports.
"""

import sys
import tomllib
from pathlib import Path

APP_DIR = Path(sys.prefix).parent
SETTINGS_FILE = APP_DIR / 'fin_research.toml'
SCREENS_FILE = APP_DIR / 'screens.toml'
FIXTURES = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures'


def _settings() -> dict:
    """The contents of fin_research.toml; empty when the file is missing."""
    return tomllib.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.is_file() else {}


def data_dir() -> Path:
    """The data folder: data_dir of fin_research.toml (relative to the app folder), or data in the app folder."""
    value = _settings().get('data_dir', '')
    return APP_DIR / Path(value).expanduser() if value else APP_DIR / 'data'


def server_port() -> int:
    """The port of the dashboard: [server] port of fin_research.toml, else 8000."""
    return int(_settings().get('server', {}).get('port', 8000))


DATA_ROOT = data_dir()
REPORTS_DIR = DATA_ROOT / 'reports'
