"""Tests for OdooClient (Epic 2 Task A2/A3)."""
import json
import urllib.error
import urllib.request

import pytest

from src.config import Settings
from src.storage.odoo_client import OdooClient, OdooError, AuthError, RateLimitError


def _settings(monkeypatch, **over):
    defaults = {
        "ODOO_URL": "https://odoo.example.com",
        "ODOO_DB": "knowledge",
        "ODOO_API_KEY": "sk-test",
    }
    defaults.update(over)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
    return Settings()


class FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeUrlopen:
    """mock urllib.request.urlopen — returns JSON-RPC result payload."""
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.last_request = None

    def __call__(self, req, context=None, timeout=None):
        self.last_request = req
        if self.error is not None:
            code, body = self.error
            raise urllib.error.HTTPError(req.full_url, code, "err", {}, body)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": self.result if self.result is not None else True,
        }
        return FakeResp(json.dumps(payload).encode())


def test_execute_kw_returns_result(monkeypatch):
    client = OdooClient(_settings(monkeypatch))
    fake = FakeUrlopen(result=5)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = client.execute_kw("res.partner", "search_count", [[]])
    assert out == 5
    # هدرهای JSON-2 باید درست باشند
    body = json.loads(fake.last_request.data)
    assert body["jsonrpc"] == "2.0"
    params = body["params"]
    assert params["service"] == "object"
    assert params["method"] == "execute_kw"
    assert params["args"][0] == "knowledge"
    assert params["args"][1] == "res.partner"
    assert params["args"][2] == "search_count"
    # هدر X-Odoo-Database + API key (urllib نام هدر را lowercase می‌کند)
    hdrs = {k.lower(): v for k, v in fake.last_request.headers.items()}
    assert hdrs.get("x-odoo-database") == "knowledge"
    assert hdrs.get("api-key") == "sk-test"


def test_http_401_raises_auth_error(monkeypatch):
    client = OdooClient(_settings(monkeypatch))
    fake = FakeUrlopen(error=(401, b'"unauthorized"'))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(AuthError):
        client.execute_kw("res.partner", "search_count", [[]])


def test_http_429_raises_rate_limit(monkeypatch):
    client = OdooClient(_settings(monkeypatch))
    fake = FakeUrlopen(error=(429, b'"too many"'))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(RateLimitError):
        client.execute_kw("res.partner", "search_count", [[]])


def test_jsonrpc_error_raises_odoo_error(monkeypatch):
    client = OdooClient(_settings(monkeypatch))
    fake = FakeUrlopen(error=(200, json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": 200, "message": "Record not found"},
    }).encode()))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(OdooError):
        client.execute_kw("res.partner", "read", [1])


def test_api_key_never_in_error_message(monkeypatch):
    client = OdooClient(_settings(monkeypatch))
    fake = FakeUrlopen(error=(401, b'"unauthorized"'))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    try:
        client.execute_kw("res.partner", "read", [1])
    except AuthError as e:
        assert "sk-test" not in str(e)
    else:
        pytest.fail("expected AuthError")


# ---------------- Task A3: retryable classification + JSON-RPC error extraction ----

from src.storage.odoo_client import _extract_error, _is_retryable


def test_is_retryable_429_5xx_timeout():
    assert _is_retryable(RateLimitError()) is True
    assert _is_retryable(
        urllib.error.HTTPError("u", 502, "bad gateway", {}, b"")
    ) is True
    assert _is_retryable(TimeoutError()) is True
    assert _is_retryable(urllib.error.URLError("dns")) is True


def test_is_retryable_4xx_auth_false():
    assert _is_retryable(
        urllib.error.HTTPError("u", 400, "bad", {}, b"")
    ) is False
    assert _is_retryable(AuthError()) is False
    assert _is_retryable(ValueError()) is False


def test_extract_error_jsonrpc_message():
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": 200, "message": "Record not found"},
    }).encode()
    assert _extract_error(body) == "Record not found"


def test_extract_error_fallback_to_text():
    assert _extract_error(b"plain error text") == "plain error text"
