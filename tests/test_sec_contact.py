"""The SEC contact address: SEC_CONTACT_EMAIL in .env (not in git), in the User-Agent of every SEC request; SEC refuses a
request without a real contact address."""

import pytest

from fin_research import config, paths


def test_the_user_agent_has_the_contact_address_of_env(tmp_path, monkeypatch) -> None:
    (tmp_path / '.env').write_text('SEC_CONTACT_EMAIL=jan.peters@example.com\n')
    monkeypatch.setattr(paths, 'PROJECT_ROOT', tmp_path)
    monkeypatch.delenv('SEC_CONTACT_EMAIL', raising=False)
    assert config.EdgarConfig().headers == {'User-Agent': 'fin_research jan.peters@example.com'}


def test_without_a_contact_address_a_request_stops_with_the_fix(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, 'PROJECT_ROOT', tmp_path)
    monkeypatch.delenv('SEC_CONTACT_EMAIL', raising=False)
    with pytest.raises(RuntimeError, match='SEC_CONTACT_EMAIL'):
        _ = config.EdgarConfig().headers


def test_a_user_agent_in_the_settings_file_wins(tmp_path, monkeypatch) -> None:
    settings = tmp_path / 'fin_research.toml'
    settings.write_text("[edgar]\nuser_agent = 'other_app jan.peters@example.com'\n")
    assert config.read(settings).edgar.headers == {'User-Agent': 'other_app jan.peters@example.com'}


def test_the_settings_file_in_git_has_no_contact_address() -> None:
    from pathlib import Path

    assert '\nuser_agent' not in (Path(__file__).parents[1] / 'fin_research.toml').read_text()
