"""Build the GUI into src/fin_research/web/static when a wheel is built, so an install from git serves the page too."""

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class GuiBuildHook(BuildHookInterface):
    """Run npm in frontend/ before the wheel is made, and put the built page (git ignores it) in the wheel."""

    def initialize(self, version: str, build_data: dict) -> None:
        """Build the page once for a normal wheel; an editable install (development) uses the run script's build."""
        if version == 'editable':
            return
        frontend = Path(self.root) / 'frontend'
        if not (Path(self.root) / 'src' / 'fin_research' / 'web' / 'static' / 'index.html').is_file():
            subprocess.run(['npm', 'ci', '--silent'], cwd=frontend, check=True)
            subprocess.run(['npm', 'run', 'build', '--silent'], cwd=frontend, check=True)
        build_data['artifacts'].append('src/fin_research/web/static/')
