"""Epic 3B — entity resolution (3F-3)."""
import pytest

from psycopg.rows import dict_row

from src.config import settings
from src.storage.db_pg import (
    connect_pg,
    pg_kg_build,
    pg_kg_entity_resolve,
)


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_kg_entity_resolve_merges_synonyms():
    pg_kg_build()
    pg_kg_entity_resolve()
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT count(*) FROM ka.kg_nodes "
                "WHERE type='entity' AND key='language models'"
            )
            lang_models = cur.fetchone()["count"]
            cur.execute(
                "SELECT count(*) FROM ka.kg_nodes "
                "WHERE type='entity' AND key='llm'"
            )
            llm = cur.fetchone()["count"]

    # مترادف «language models» باید در «llm» ادغام شده باشد
    assert lang_models == 0
    assert llm == 1
