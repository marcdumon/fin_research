"""Where everything lives on disk.

One module so no other file ever builds a path from `__file__`. The config folder ~/.config/md_apps/fin_research (the repo in development) holds
`fin_research.toml`, `screens.toml` and `.env`. The app folder is the folder of the Python environment. The data folder is `data_dir` of `fin_research.toml`: the filings, the DuckDB store, the run
ledger and the reports.
"""

import sys
import tomllib
from pathlib import Path

APP_DIR = Path(sys.prefix).parent
# an installed app keeps its settings in ~/.config; a development copy (a repo) keeps its own, next to the code
CONFIG_DIR = APP_DIR if (APP_DIR / 'pyproject.toml').is_file() else Path.home() / '.config' / 'md_apps' / 'fin_research'
SETTINGS_FILE = CONFIG_DIR / 'fin_research.toml'
SCREENS_FILE = CONFIG_DIR / 'screens.toml'
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
