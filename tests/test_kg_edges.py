"""Epic 3B — knowledge graph edges (3F-2)."""
import pytest

from psycopg.rows import dict_row

from src.config import settings
from src.storage.db_pg import connect_pg, pg_kg_build


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_kg_edges_populated_and_relates_to_exists():
    pg_kg_build()
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT count(*) FROM ka.kg_edges")
            total = cur.fetchone()["count"]
            cur.execute(
                """
                SELECT e.id FROM ka.kg_edges e
                JOIN ka.kg_nodes s ON s.id = e.src_id
                JOIN ka.kg_nodes d ON d.id = e.dst_id
                WHERE e.rel='RELATES_TO' AND s.type='document' AND d.type='document'
                LIMIT 1
                """
            )
            relates = cur.fetchone()

    assert total > 0
    assert relates is not None  # حداقل یک RELATES_TO بین دو سند هم‌مفهوم
