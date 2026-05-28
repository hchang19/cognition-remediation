import pytest
import requests
from unittest.mock import MagicMock

from app.db import get_db


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: offline, fully mocked — no network or filesystem side-effects")
    config.addinivalue_line("markers", "integration: requires network and .env credentials")


@pytest.fixture
def mem_db():
    conn = get_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def mock_session():
    session = MagicMock(spec=requests.Session)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}
    response.headers = {}
    response.raise_for_status.return_value = None
    session.get.return_value = response
    session.post.return_value = response
    return session
