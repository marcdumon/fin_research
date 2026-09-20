"""Where everything lives on disk.

One module so no other file ever builds a path from `__file__`. Kept deliberately tiny: the data root is
overridable through config, and this only supplies the default.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / 'data'
FIXTURES = PROJECT_ROOT / 'tests' / 'fixtures'
