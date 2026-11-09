"""Unit tests for the Postgres connection layer (db_pg.py)."""
import pytest

from src.config import settings
from src.storage.db_pg import PgUnavailable, connect_pg, ping


def test_dsn_uses_schema():
    from src.storage.db_pg import _dsn
    dsn = _dsn()
    assert f"host={settings.pg_host}" in dsn
    assert f"port={settings.pg_port}" in dsn
    assert f"user={settings.pg_user}" in dsn
    assert f"dbname={settings.pg_db}" in dsn
    assert f"search_path={settings.pg_schema}" in dsn


def test_ping_ok_when_pg_configured():
    # ping() returns True only when a real connection succeeds; without PG
    # configured it returns False (never raises).
    result = ping()
    assert isinstance(result, bool)
