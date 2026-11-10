"""Tests for Odoo config fields (Epic 2 Task A1)."""
import pytest
from pydantic import ValidationError

from src.config import Settings


def test_odoo_defaults_disabled():
    s = Settings(_env_file=None)
    assert s.odoo_enabled is False
    assert s.odoo_url == ""
    assert s.odoo_db == ""
    assert s.odoo_api_key == ""


def test_odoo_parses_from_env(monkeypatch):
    monkeypatch.setenv("ODOO_ENABLED", "true")
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.com")
    monkeypatch.setenv("ODOO_DB", "knowledge")
    monkeypatch.setenv("ODOO_API_KEY", "secret-key")
    monkeypatch.setenv("ODOO_UID", "6")
    s = Settings(_env_file=None)
    assert s.odoo_enabled is True
    assert s.odoo_url == "https://odoo.example.com"
    assert s.odoo_db == "knowledge"
    assert s.odoo_api_key == "secret-key"
    assert s.odoo_uid == 6


def test_odoo_enabled_requires_config(monkeypatch):
    # enabled بدون url/db/key/uid → باید ValidationError بدهد
    monkeypatch.setenv("ODOO_ENABLED", "true")
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ODOO_DB", raising=False)
    monkeypatch.delenv("ODOO_API_KEY", raising=False)
    monkeypatch.delenv("ODOO_UID", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
