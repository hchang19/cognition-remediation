import hashlib
import hmac
import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.shared.config import Config


def _make_app(webhook_secret: str | None = None):
    from app.main import create_app
    from app.db import get_db
    cfg = Config(
        github_token="t", github_repo="o/r",
        github_webhook_secret=webhook_secret,
        devin_api_key="k", devin_org_id="org-test", devin_daily_limit=10,
        pause=False, db_path=":memory:",
        devin_session_cost_limit_usd=None,
        devin_session_time_limit_minutes=None,
    )
    db = get_db(":memory:")
    devin = MagicMock(spec=DevinClient)
    devin.create_session.return_value = "sess-1"
    gh = MagicMock(spec=GitHubClient)
    app = create_app(cfg=cfg, db=db, devin=devin, gh=gh)
    # Use raise_server_exceptions=False so we can inspect error responses
    client = TestClient(app, raise_server_exceptions=True)
    # Trigger lifespan so app.state is populated
    client.__enter__()
    return client, db, devin, gh


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.unit
def test_healthz_returns_ok():
    client, *_ = _make_app()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.unit
def test_webhook_invalid_signature_returns_403():
    client, *_ = _make_app(webhook_secret="secret")
    body = json.dumps({"action": "opened"}).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=bad", "X-GitHub-Event": "issues"},
    )
    assert resp.status_code == 403


@pytest.mark.unit
def test_webhook_no_secret_skips_signature_check():
    client, _, devin, _ = _make_app(webhook_secret=None)
    body = json.dumps({
        "action": "opened",
        "issue": {"number": 1, "title": "T", "body": "", "labels": [{"name": "auto-remediate"}, {"name": "complexity:definite"}, {"name": "source:manual"}]},
    }).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-GitHub-Event": "issues"},
    )
    assert resp.status_code == 200


@pytest.mark.unit
def test_webhook_issues_opened_without_auto_remediate_does_not_dispatch():
    client, _, devin, _ = _make_app()
    body = json.dumps({
        "action": "opened",
        "issue": {"number": 2, "title": "T", "body": "", "labels": []},
    }).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": "", "X-GitHub-Event": "issues"},
    )
    assert resp.status_code == 200
    devin.create_session.assert_not_called()


@pytest.mark.unit
def test_webhook_valid_signature_accepted():
    secret = "mysecret"
    client, _, devin, _ = _make_app(webhook_secret=secret)
    body = json.dumps({
        "action": "opened",
        "issue": {"number": 3, "title": "T", "body": "", "labels": [{"name": "auto-remediate"}, {"name": "complexity:definite"}, {"name": "source:manual"}]},
    }).encode()
    sig = _sign(body, secret)
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "issues"},
    )
    assert resp.status_code == 200
