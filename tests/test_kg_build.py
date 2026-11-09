"""Epic 3B — knowledge graph node build (3F-1)."""
import pytest

from psycopg.rows import dict_row

from src.config import settings
from src.storage.db_pg import connect_pg, pg_kg_build


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_kg_build_populates_nodes():
    pg_kg_build()
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT count(*) FROM ka.kg_nodes")
            total = cur.fetchone()["count"]
            cur.execute(
                "SELECT id FROM ka.kg_nodes WHERE type='concept' AND key='rag'"
            )
            rag = cur.fetchone()
            cur.execute("SELECT count(*) FROM ka.kg_nodes WHERE type='document'")
            docs = cur.fetchone()["count"]
            cur.execute("SELECT count(*) FROM ka.kg_nodes WHERE type='entity'")
            ents = cur.fetchone()["count"]

    assert total > 0
    assert rag is not None  # مفهوم «rag» باید به‌عنوان نود concept باشد
    assert docs >= 1
    assert ents >= 1  # موجودیت از tags/title استخراج شده باشد
