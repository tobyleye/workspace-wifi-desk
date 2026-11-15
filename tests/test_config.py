import pytest

from app import config
from app.config import ConfigError, Settings

GOOGLE_VARS = ("SPREADSHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SERVICE_ACCOUNT_FILE")


@pytest.fixture
def clean_env(monkeypatch):
    for var in GOOGLE_VARS + ("MCP_TOKEN", "ADMIN_TOKEN", "PORT", "CACHE_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_fails_listing_every_missing_variable(clean_env):
    with pytest.raises(ConfigError) as exc:
        Settings.from_env()
    message = str(exc.value)
    assert "SPREADSHEET_ID is not set" in message
    assert "GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE is not set" in message
    assert isinstance(exc.value, SystemExit)  # stops the program with the message, no traceback


def test_fails_when_key_file_is_missing(clean_env, tmp_path):
    clean_env.setenv("SPREADSHEET_ID", "sheet")
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(tmp_path / "nope.json"))
    with pytest.raises(ConfigError, match="missing file"):
        Settings.from_env()


def test_reads_values_and_defaults(clean_env, tmp_path):
    key = tmp_path / "key.json"
    key.write_text("{}")
    clean_env.setenv("SPREADSHEET_ID", "sheet")
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(key))
    s = Settings.from_env()
    assert (s.spreadsheet_id, s.port, s.cache_seconds, s.mcp_token) == ("sheet", 8000, 5.0, "")


def test_load_reads_dotenv_but_real_env_wins(clean_env, tmp_path):
    from dotenv import load_dotenv as real_load_dotenv

    clean_env.setattr(config, "load_dotenv", real_load_dotenv)  # undo the conftest guard
    (tmp_path / ".env").write_text(
        "SPREADSHEET_ID=from-dotenv\nGOOGLE_SERVICE_ACCOUNT_JSON={}\nMCP_TOKEN=from-dotenv\n"
    )
    clean_env.chdir(tmp_path)
    clean_env.setenv("MCP_TOKEN", "from-env")
    s = Settings.load()
    assert s.spreadsheet_id == "from-dotenv"
    assert s.mcp_token == "from-env"
    for var in GOOGLE_VARS + ("MCP_TOKEN",):
        clean_env.delenv(var, raising=False)  # load_dotenv wrote to os.environ
