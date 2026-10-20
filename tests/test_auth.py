"""تست احراز هویت وب — سرویس بدون توکن باید همه چیز را ببندد (fail-closed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-abc123"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("WEB_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from src import config

    monkeypatch.setattr(config.settings, "web_auth_token", TOKEN, raising=False)
    from src.main import app

    return TestClient(app, follow_redirects=False)


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_requires_auth(client):
    assert client.get("/api/docs").status_code == 401


def test_wrong_token_rejected(client):
    r = client.get("/api/docs", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_bearer_token_allowed(client):
    r = client.get("/api/docs", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_query_token_allowed(client):
    assert client.get(f"/api/docs?token={TOKEN}").status_code == 200


def test_html_redirects_to_login(client):
    r = client.get("/index", headers={"Accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_login_sets_cookie_and_grants_access(client):
    bad = client.post("/login", data={"token": "wrong", "next": "/index"})
    assert bad.status_code == 401

    ok = client.post("/login", data={"token": TOKEN, "next": "/index"})
    assert ok.status_code == 303
    assert "ka_auth" in ok.cookies
    # کوکی مقدار خام توکن را ندارد
    assert TOKEN not in ok.cookies["ka_auth"]

    granted = client.get("/api/docs", cookies={"ka_auth": ok.cookies["ka_auth"]})
    assert granted.status_code == 200


def test_fail_closed_without_configured_token(monkeypatch):
    from src import config
    from src.main import app

    monkeypatch.setattr(config.settings, "web_auth_token", "", raising=False)
    c = TestClient(app, follow_redirects=False)
    assert c.get("/api/docs").status_code == 503
