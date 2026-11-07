"""Integration test against the real Odoo `ka_test` DB (Epic 2 E2).

Skipped when the live credentials are not reachable (e.g. CI without the
test environment). The API key is read from .env.test without mutating the
process environment (parsed directly, no load_dotenv), so other tests stay
isolated.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integration


def _read_env_test() -> dict:
    """Parse .env.test lines into a dict (no os.environ mutation)."""
    p = ROOT / ".env.test"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _make_settings():
    vals = _read_env_test()
    if not vals.get("ODOO_ENABLED", "").lower() in ("1", "true", "yes"):
        return None
    from src.config import Settings

    return Settings(
        odoo_url=vals.get("ODOO_URL", ""),
        odoo_db=vals.get("ODOO_DB", ""),
        odoo_api_key=vals.get("ODOO_API_KEY", ""),
        odoo_uid=int(vals.get("ODOO_UID", "0") or 0),
    )


@pytest.fixture(scope="module")
def client():
    cfg = _make_settings()
    if cfg is None or not cfg.odoo_uid:
        pytest.skip("Odoo test env not enabled/configured")
    from src.storage.odoo_client import OdooClient

    return OdooClient(cfg)


def test_oive_connectivity(client):
    # reachable + readable model
    assert client.execute_kw("res.partner", "search_count", [[]]) >= 0


def test_knowledge_article_model_exists(client):
    n = client.execute_kw("knowledge.article", "search_count", [[]])
    assert isinstance(n, int)
    rows = client.execute_kw(
        "knowledge.article", "search_read", [[]], {"fields": ["name"], "limit": 1}
    )
    assert isinstance(rows, list)
    for r in rows:
        assert "name" in r


def test_article_properties_field_present(client):
    # The `article_properties` field is a `properties` type on the model.
    # Confirm the field exists via ir.model.fields (if the user can read it).
    try:
        fields = client.execute_kw(
            "ir.model.fields",
            "search_read",
            [[["model", "=", "knowledge.article"], ["name", "=", "article_properties"]]],
            {"fields": ["name", "ttype"]},
        )
    except Exception:
        pytest.skip("user lacks access to ir.model.fields")
    assert isinstance(fields, list)
    assert any(f.get("name") == "article_properties" for f in fields)
