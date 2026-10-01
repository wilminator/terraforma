"""settings.toml: the example is valid, mistakes are caught, and the migrate command uses it."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from terraforma.__main__ import main
from terraforma.settings import load_settings

ROOT = Path(__file__).parent.parent


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text)
    return path


def test_the_example_settings_file_is_valid():
    settings = load_settings(ROOT / "settings.example.toml")
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.mail is None, "mail is commented out in the example"


def test_a_misspelled_setting_is_an_error_not_ignored(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(write(tmp_path, 'session_secret = "' + "x" * 32 + '"\nsecure_cookie = false\n'))


def test_a_short_session_secret_is_refused(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(write(tmp_path, 'session_secret = "short"\n'))


def test_mail_defaults_to_implicit_tls_on_465(tmp_path):
    settings = load_settings(write(tmp_path, 'session_secret = "' + "x" * 32 + '"\n'
                                   '[mail]\nhost = "smtp.example.com"\nusername = "k"\npassword = "k"\nfrom_address = "a@b.c"\n'))
    assert (settings.mail.port, settings.mail.implicit_tls) == (465, True)


def test_the_migrate_command_builds_the_tables(tmp_path, monkeypatch, capsys):
    database = tmp_path / "game.db"
    path = write(tmp_path, f'database_url = "sqlite+aiosqlite:///{database}"\nsession_secret = "' + "x" * 32 + '"\n')
    monkeypatch.setenv("TERRAFORMA_SETTINGS", str(path))
    assert main(["terraforma", "check-settings"]) == 0
    assert main(["terraforma", "migrate"]) == 0
    assert main(["terraforma", "migrate"]) == 0, "running it again changes nothing"
    tables = {row[0] for row in sqlite3.connect(database).execute("select name from sqlite_master where type = 'table'")}
    assert {"accounts", "worlds", "maps", "fighters", "alembic_version"} <= tables
    assert main(["terraforma", "nonsense"]) == 2
