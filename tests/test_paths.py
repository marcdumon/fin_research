"""The app folder (fin_research.toml, screens.toml and .env, next to the Python environment) and the data folder (the
filings, the DuckDB store, the run ledger and the reports; data_dir of fin_research.toml)."""

import sys
from pathlib import Path

from fin_research import config, paths


def _settings(tmp_path: Path, monkeypatch, text: str) -> Path:
    """Point the app to a fin_research.toml with this text."""
    settings = tmp_path / 'fin_research.toml'
    settings.write_text(text)
    monkeypatch.setattr(paths, 'SETTINGS_FILE', settings)
    return settings


def test_the_app_folder_holds_the_python_environment() -> None:
    assert Path(sys.prefix).parent == paths.APP_DIR


def test_the_data_folder_is_data_dir_of_the_settings(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch, f"data_dir = '{tmp_path / 'backed_up'}'\n")
    assert paths.data_dir() == tmp_path / 'backed_up'
    assert config.read(settings).data_root == tmp_path / 'backed_up'


def test_a_data_dir_that_starts_with_a_tilde_is_in_the_home_folder(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch, "data_dir = '~/fin_research'\n")
    assert paths.data_dir() == Path.home() / 'fin_research'


def test_without_data_dir_the_data_folder_is_in_the_app_folder(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch, "data_dir = ''\n")
    assert paths.data_dir() == paths.APP_DIR / 'data'
    monkeypatch.setattr(paths, 'SETTINGS_FILE', tmp_path / 'missing.toml')
    assert paths.data_dir() == paths.APP_DIR / 'data'


def test_the_port_is_in_the_server_part_of_the_settings(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch, '[server]\nport = 18000\n')
    assert paths.server_port() == 18000
    monkeypatch.setattr(paths, 'SETTINGS_FILE', tmp_path / 'missing.toml')
    assert paths.server_port() == 8000


def test_the_settings_are_in_the_app_folder_and_the_reports_in_the_data_folder() -> None:
    assert paths.SETTINGS_FILE == paths.APP_DIR / 'fin_research.toml'
    assert paths.SCREENS_FILE == paths.APP_DIR / 'screens.toml'
    assert paths.REPORTS_DIR == paths.DATA_ROOT / 'reports'


def test_the_example_settings_have_an_empty_data_dir() -> None:
    assert "\ndata_dir = ''\n" in (Path(__file__).parents[1] / 'fin_research.example.toml').read_text()
