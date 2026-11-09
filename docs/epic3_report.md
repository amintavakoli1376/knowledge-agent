# Epic 3 — Migration Report (SQLite → Postgres schema `ka`)

**Status:** complete ✦ 139 tests passing (1 skip), diff_report CLEAN.

## What changed
- Knowledge source of truth moved from SQLite (`data/knowledge.db`) to Postgres
  schema `ka` on the same Odoo PG cluster.
- `odoo-db` image upgraded postgres:16 → `pgvector/pgvector:pg16` and exposed on
  `127.0.0.1:5544` (localhost only). `CREATE EXTENSION vector` (0.8.7).
- `ka` tables: content (with generated `search_ts` tsvector + GIN index),
  doc_versions, embeddings (pgvector), concepts. Pgvector 0.8.7.

## Tasks
| Task | Description | Status |
|------|-------------|--------|
| 3A-1 | infra: pgvector image, schema ka | done |
| 3A-2 | PG connection layer (`db_pg.py`) | done |
| 3B-1 | data migration (`migrate_to_pg.py`, idempotent) | done |
| 3B-2 | paper search FTS5 → tsvector (`api_views.py`) | done |
| 3C-1 | dual-write (`pg_mirror.py`), all 4 save points | done |
| 3C-2 | daily divergence report (`diff_report.py`) | done |
| 3D-1 | cutover read paths → PG (get_by_url/count/search/embed) | done |
| 3D-2 | E2E tests + PG-down behavior (`mode: pg_err`) | done |
| 3E-1 | SQLite retired as source of truth (archived) | done |
| 3E-2 | hybrid search FR-009: vector + FTS + graph + rerank | done |

## Hybrid search (3E-2)
- `vector_store.search` reads `ka.embeddings` via pgvector `<=>` cosine.
- `fts_search` reads `ka.content.search_ts` (tsvector, GIN).
- `_graph_expand` reads `ka.concepts` (doc_ids remapped to PG ids; negative
  url-hashes resolved against current ka.content urls).
- Rerank: `0.75*vector + 0.25*min(fts,1) + 0.05` when FTS matches.

## Bugs found & fixed
- **embeddings.content_id orphaned**: migrated rows kept old SQLite ids while
  PG content.id got fresh serials → vector search returned nothing. Fixed by
  mapping old→new id via url; migration script now builds the same map.
- **concepts.doc_ids orphaned**: same issue — remapped all 100 concept rows.
- **test pollution**: `test_pg_store_chunks_roundtrip` rewrote a real doc's
  chunks; now uses an isolated test URL and cleans up. `test_odoo_wiring`
  disables pg_enabled so it doesn't write to real PG.

## Counts (after reconciliation)
content 27 / doc_versions 27 (29 in SQLite; 2 are legacy exact duplicates,
deduped — distinct-key match) / embeddings 229 / concepts 100.

## Rollback
Flip `PG_ENABLED=false` in `.env`: all read paths fall back to the SQLite
originals (still intact and archived). Dual-write mirror stays write-only.

## Backups
`/root/backups/odoo_pre_pgvector/{production,ka_test,postgres}.dump`
`/root/backups/sqlite_archive/knowledge.db.<ts>.bak`
