import pytest


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Tests never read a developer's real .env (it could hold real credentials)."""
    monkeypatch.setattr("app.config.load_dotenv", lambda *a, **k: False)
