"""Tests for Epic 2 / 2F-1: Odoo mirror wiring in the Telegram bot.

We test the `_save_odoo` helper in isolation (constructed via `__new__` to
skip `__init__`, which needs a bot token). The helper imports `OdooStorage`
lazily inside the function, so we patch `src.storage.odoo.OdooStorage`.
Gates under test:
  1. ODOO_ENABLED=false -> returns None, never calls OdooStorage.
  2. ODOO_ENABLED=true  -> calls OdooStorage().save in a thread, returns its URL.
  3. Any error inside OdooStorage.save is swallowed -> returns None (non-fatal).
"""
import asyncio

from src.gateway.telegram_bot import TelegramBot


def make_bot():
    """Instantiate a TelegramBot without running __init__."""
    return TelegramBot.__new__(TelegramBot)


def _fake_settings(enabled):
    return type("S", (), {"odoo_enabled": enabled})()


def test_disabled_returns_none_and_no_call(monkeypatch):
    bot = make_bot()

    def boom(self, content, analysis):
        raise AssertionError("OdooStorage must NOT be called when disabled")

    monkeypatch.setattr("src.gateway.telegram_bot.settings", _fake_settings(False))
    monkeypatch.setattr("src.storage.odoo.OdooStorage", type("Boom", (), {"save": boom}))

    result = asyncio.run(bot._save_odoo("content", "analysis"))
    assert result is None


def test_enabled_returns_url(monkeypatch):
    bot = make_bot()
    fake_cls = type("FakeOdooStorage", (), {})
    setattr(fake_cls, "save", staticmethod(lambda c, a: "http://odoo.local/ka/42"))

    monkeypatch.setattr("src.gateway.telegram_bot.settings", _fake_settings(True))
    monkeypatch.setattr("src.storage.odoo.OdooStorage", fake_cls)

    result = asyncio.run(bot._save_odoo("content", "analysis"))
    assert result == "http://odoo.local/ka/42"


def test_save_error_swallowed_returns_none(monkeypatch):
    bot = make_bot()

    def raiser(self, c, a):
        raise RuntimeError("odoo down")

    monkeypatch.setattr("src.gateway.telegram_bot.settings", _fake_settings(True))
    monkeypatch.setattr("src.storage.odoo.OdooStorage", type("Raising", (), {"save": raiser}))

    result = asyncio.run(bot._save_odoo("content", "analysis"))
    assert result is None
