"""Postgres connection layer for the migration target (schema `ka`).

Epic 3: the knowledge source-of-truth moves from SQLite to Postgres
(schema `ka` on the same Odoo PG cluster). This module mirrors
`db.py`'s API so callers are agnostic to the backend during dual-write
and cutover.

Connection parameters come from `settings` (PG_* env vars) so the
enabled/failover logic stays one place.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from ..config import settings

logger = logging.getLogger(__name__)


def _dsn() -> str:
    return (
        f"host={settings.pg_host} port={settings.pg_port} "
        f"user={settings.pg_user} dbname={settings.pg_db} "
        f"options=-csearch_path={settings.pg_schema},public"
    )


def connect_pg() -> psycopg.Connection:
    """Open a new connection, search_path pinned to the `ka` schema."""
    conn = psycopg.connect(
        _dsn(),
        password=settings.pg_password or None,
        autocommit=False,
    )
    return conn


class PgUnavailable(Exception):
    """Raised when PG is expected but unreachable (fail-fast in dual-write)."""


def ping() -> bool:
    """True if a round-trip to PG succeeds."""
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1
    except Exception as e:  # noqa: BLE001
        logger.warning("PG ping failed: %s", e)
        return False


def pg_get_by_url(url: str) -> dict | None:
    """Return the ka.content row for a URL (same shape as sqlite get_by_url)."""
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM ka.content WHERE url=%s LIMIT 1", (url,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("PG get_by_url failed: %s", e)
        return None


def pg_count_content() -> int:
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ka.content")
                return cur.fetchone()[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("PG count_content failed: %s", e)
        return 0


def pg_vector_search(
    qv: list[float], limit: int = 5, min_score: float = 0.3
) -> list[dict]:
    """Cosine-similarity search over ka.embeddings via pgvector (<=>).

    Mirrors vector_store.search()'s result shape (chunk text + doc meta).
    Only rows scoring >= min_score are returned, ranked desc.
    """
    vec_str = "[" + ",".join(f"{float(x):.6f}" for x in qv) + "]"
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT e.content_id, e.chunk_index, e.chunk_text,
                           c.url, c.title, c.platform, c.category,
                           COALESCE(c.summary_fa,'') AS summary_fa,
                           COALESCE(c.saved_at,'') AS saved_at,
                           (1 - (e.vector <=> %s::vector)) AS score
                    FROM ka.embeddings e
                    JOIN ka.content c ON c.id = e.content_id
                    WHERE (1 - (e.vector <=> %s::vector)) >= %s
                    ORDER BY e.vector <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_str, vec_str, min_score, vec_str, limit),
                )
                rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "content_id": r["content_id"],
                    "chunk_index": r["chunk_index"],
                    "chunk_text": r["chunk_text"],
                    "score": round(float(r["score"]), 4),
                    "url": r["url"],
                    "title": r["title"],
                    "platform": r["platform"],
                    "category": r["category"],
                    "summary_fa": r["summary_fa"],
                    "saved_at": r["saved_at"],
                }
            )
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("PG vector search failed: %s", e)
        return []


def pg_get_content_id_by_url(url: str) -> int | None:
    """Return ka.content.id for a URL (None if absent)."""
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ka.content WHERE url=%s", (url,))
                row = cur.fetchone()
                return int(row[0]) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("PG get content_id failed: %s", e)
        return None


def pg_store_chunks(content_id: int, chunks: list[str], vectors: list) -> None:
    """Replace all embedding chunks for a ka.content row (idempotent)."""
    if not chunks or not vectors:
        return
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ka.embeddings WHERE content_id=%s", (content_id,))
                now = datetime.now(timezone.utc).isoformat()
                for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
                    vec_str = "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
                    cur.execute(
                        """
                        INSERT INTO ka.embeddings
                            (content_id, chunk_index, chunk_text, vector, created_at)
                        VALUES (%s,%s,%s,%s::vector,%s)
                        """,
                        (content_id, i, chunk, vec_str, now),
                    )
                conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("PG store_chunks failed: %s", e)


def pg_fts_search(query: str, limit: int = 5) -> list[dict]:
    """Keyword search over ka.content.search_ts (tsvector) — mirrors sqlite fts_search."""
    import re

    tokens = re.findall(r"[\w\u0600-\u06FF]+", query)
    if not tokens:
        return []
    # websearch_to_tsquery: OR ضمنی، -، "…"
    q = " OR ".join(tokens[:8])
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT c.id AS content_id, c.url, c.title, c.platform, c.category,
                           COALESCE(c.summary_fa,'') AS summary_fa, c.full_text,
                           ts_rank(c.search_ts, websearch_to_tsquery('simple', %s)) AS score
                    FROM ka.content c
                    WHERE c.search_ts @@ websearch_to_tsquery('simple', %s)
                    ORDER BY score DESC, c.saved_at DESC
                    LIMIT %s
                    """,
                    (q, q, limit),
                )
                rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "content_id": r["content_id"],
                    "url": r["url"],
                    "title": r["title"],
                    "platform": r["platform"],
                    "category": r["category"],
                    "summary_fa": r["summary_fa"],
                    "snippet": (r["full_text"] or "")[:300],
                    "score": round(float(r["score"]), 4),
                }
            )
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("PG FTS search failed: %s", e)
        return []


def pg_full_texts(content_ids: list[int]) -> dict[int, str]:
    """Full text for content ids (dict id -> full_text)."""
    if not content_ids:
        return {}
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                # psycopg: ساختن ردیف‌های پارامتر
                marks = ",".join(["%s"] * len(content_ids))
                cur.execute(
                    f"SELECT id, COALESCE(full_text,'') FROM ka.content WHERE id IN ({marks})",
                    list(content_ids),
                )
                return {int(r[0]): r[1] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("PG full_texts failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Knowledge Graph (Epic 3B) — build / entity-resolve / expand
# ---------------------------------------------------------------------------


def _parse_tags(tags) -> list[str]:
    """content.tags is stored as a JSON text array (or a list); tolerate both."""
    if tags is None:
        return []
    if isinstance(tags, list):
        return [t for t in tags if isinstance(t, str)]
    if isinstance(tags, str):
        s = tags.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                v = json.loads(s)
                if isinstance(v, list):
                    return [t for t in v if isinstance(t, str)]
            except Exception:  # noqa: BLE001
                pass
        return [t.strip() for t in s.split(",") if t.strip()]
    return []


def _extract_entities(title, tags) -> list[str]:
    """Named-entity candidates from a document's tags + capitalized title runs."""
    ents: list[str] = []
    for t in _parse_tags(tags):
        e = t.strip().lower()
        if e and e not in ents:
            ents.append(e)
    if title:
        for m in re.finditer(
            r"\b[A-Z][A-Za-z0-9'\-]*(?:\s+[A-Z][A-Za-z0-9'\-]*)*\b", title
        ):
            w = m.group(0).strip()
            if len(w) >= 3:
                e = w.lower()
                if e not in ents:
                    ents.append(e)
    return ents


def pg_kg_build() -> dict:
    """Fill kg_nodes (concept/entity/document) and kg_edges (RELATES_TO,
    MENTIONS, DISCUSSES). Idempotent — safe to re-run.

    - concept nodes: 1:1 with ka.concepts (key=norm, label=display)
    - document nodes: 1:1 with ka.content (key='doc:<content.id>', label=title)
    - entity nodes:   extracted from each doc's tags + capitalized title words
    Edges:
    - RELATES_TO: between every pair of documents sharing a concept (doc_count>=2)
    - MENTIONS:   document → entity, for each entity extracted from that document
    - DISCUSSES:  between documents sharing >= 2 concepts (weight = shared count)
    """
    stats = {
        "concepts": 0, "documents": 0, "entities": 0,
        "relates_to": 0, "discusses": 0, "mentions": 0,
    }
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                # ---- concept nodes ----
                cur.execute(
                    """
                    INSERT INTO ka.kg_nodes (type, key, label)
                    SELECT 'concept', norm, display FROM ka.concepts
                    ON CONFLICT (type, key) DO NOTHING
                    """
                )
                stats["concepts"] = cur.rowcount

                # ---- document nodes ----
                cur.execute(
                    """
                    INSERT INTO ka.kg_nodes (type, key, label)
                    SELECT 'document', 'doc:' || id::text,
                           COALESCE(NULLIF(title,''), 'untitled')
                    FROM ka.content
                    ON CONFLICT (type, key) DO NOTHING
                    """
                )
                stats["documents"] = cur.rowcount

                doc_node: dict[int, int] = {}
                cur.execute("SELECT id, key FROM ka.kg_nodes WHERE type='document'")
                for nid, key in cur.fetchall():
                    if key.startswith("doc:"):
                        doc_node[int(key[4:])] = int(nid)

                # ---- entity extraction (per doc) ----
                entity_pairs: list[tuple[int, str]] = []
                cur.execute("SELECT id, title, tags FROM ka.content")
                for cid, title, tags in cur.fetchall():
                    for e in _extract_entities(title, tags):
                        entity_pairs.append((int(cid), e))
                for e in dict.fromkeys(k for _, k in entity_pairs):
                    cur.execute(
                        """
                        INSERT INTO ka.kg_nodes (type, key, label)
                        VALUES ('entity', %s, %s)
                        ON CONFLICT (type, key) DO NOTHING
                        """,
                        (e, e),
                    )
                entity_node: dict[str, int] = {}
                cur.execute("SELECT id, key FROM ka.kg_nodes WHERE type='entity'")
                for nid, key in cur.fetchall():
                    entity_node[key] = int(nid)
                stats["entities"] = len(entity_node)

                # ---- concept → doc co-occurrence ----
                cur.execute("SELECT doc_ids FROM ka.concepts WHERE doc_count >= 2")
                relates: dict[tuple[int, int], int] = {}
                for (docids,) in cur.fetchall():
                    mids = [
                        int(x)
                        for x in (docids or "").split(",")
                        if x.strip().isdigit()
                    ]
                    for i in range(len(mids)):
                        for j in range(i + 1, len(mids)):
                            a, b = mids[i], mids[j]
                            k = (a, b) if a < b else (b, a)
                            relates[k] = relates.get(k, 0) + 1

                # ---- RELATES_TO (docs share >= 1 concept) ----
                for (a, b), _cnt in relates.items():
                    na, nb = doc_node.get(a), doc_node.get(b)
                    if not na or not nb or na == nb:
                        continue
                    s, d = (na, nb) if na < nb else (nb, na)
                    cur.execute(
                        """
                        INSERT INTO ka.kg_edges (src_id, dst_id, rel, weight)
                        VALUES (%s,%s,'RELATES_TO',1.0)
                        ON CONFLICT (src_id, dst_id, rel) DO NOTHING
                        """,
                        (s, d),
                    )
                    stats["relates_to"] += 1

                # ---- DISCUSSES (docs share >= 2 concepts) ----
                for (a, b), cnt in relates.items():
                    if cnt < 2:
                        continue
                    na, nb = doc_node.get(a), doc_node.get(b)
                    if not na or not nb or na == nb:
                        continue
                    s, d = (na, nb) if na < nb else (nb, na)
                    cur.execute(
                        """
                        INSERT INTO ka.kg_edges (src_id, dst_id, rel, weight)
                        VALUES (%s,%s,'DISCUSSES',%s)
                        ON CONFLICT (src_id, dst_id, rel) DO NOTHING
                        """,
                        (s, d, float(cnt)),
                    )
                    stats["discusses"] += 1

                # ---- MENTIONS (document → entity) ----
                for cid, ekey in entity_pairs:
                    nd = doc_node.get(cid)
                    ne = entity_node.get(ekey)
                    if not nd or not ne:
                        continue
                    cur.execute(
                        """
                        INSERT INTO ka.kg_edges (src_id, dst_id, rel, weight, doc_id)
                        VALUES (%s,%s,'MENTIONS',1.0,%s)
                        ON CONFLICT (src_id, dst_id, rel) DO NOTHING
                        """,
                        (nd, ne, str(cid)),
                    )
                    stats["mentions"] += 1

                conn.commit()
        return stats
    except Exception as e:  # noqa: BLE001
        logger.warning("PG kg build failed: %s", e)
        return {}


# Rule-based synonym groups: lowercase variant → canonical key (same node type).
KG_SYNONYMS = {
    "llm": ["llm", "language models", "large language model"],
    "ai": ["ai", "artificial-intelligence", "artificial intelligence"],
    "rag": ["rag", "retrieval augmented generation", "retrieval augmented"],
}


def pg_kg_entity_resolve() -> int:
    """Merge synonym nodes in kg_nodes (rule-based, same-type only).

    For each node whose key matches a synonym variant, its edges are rewired
    onto the canonical node and the synonym node is deleted. Returns the number
    of nodes merged (0 when a group is already resolved).
    """
    alias_to_canon: dict[str, str] = {}
    canon_label: dict[str, str] = {}
    for canon, variants in KG_SYNONYMS.items():
        base = canon.lower()
        canon_label[base] = canon
        for v in variants:
            alias_to_canon[v.lower()] = base

    merged = 0
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id, type, key, label FROM ka.kg_nodes")
                nodes = cur.fetchall()
                by_type_key: dict[tuple[str, str], int] = {}
                for n in nodes:
                    by_type_key[(n["type"], n["key"])] = int(n["id"])

                for n in nodes:
                    ntype, nkey = n["type"], n["key"]
                    low = nkey.lower()
                    canon = alias_to_canon.get(low)
                    if canon is None or canon == low:
                        continue
                    victim = int(n["id"])
                    target_id = by_type_key.get((ntype, canon))
                    if target_id is None:
                        cur.execute(
                            """
                            INSERT INTO ka.kg_nodes (type, key, label)
                            VALUES (%s,%s,%s)
                            ON CONFLICT (type, key) DO NOTHING
                            RETURNING id
                            """,
                            (ntype, canon, canon_label[canon]),
                        )
                        row = cur.fetchone()
                        target_id = (
                            int(row["id"])
                            if row
                            else by_type_key.get((ntype, canon))
                        )
                        if target_id is None:
                            cur.execute(
                                "SELECT id FROM ka.kg_nodes WHERE type=%s AND key=%s",
                                (ntype, canon),
                            )
                            r = cur.fetchone()
                            if r:
                                target_id = int(r["id"])
                        if target_id is None or target_id == victim:
                            continue
                        by_type_key[(ntype, canon)] = target_id

                    cur.execute(
                        """
                        SELECT id, src_id, dst_id, rel, weight, doc_id
                        FROM ka.kg_edges WHERE src_id=%s OR dst_id=%s
                        """,
                        (victim, victim),
                    )
                    moving = cur.fetchall()
                    cur.execute(
                        "DELETE FROM ka.kg_edges WHERE src_id=%s OR dst_id=%s",
                        (victim, victim),
                    )
                    for e in moving:
                        src, dst = int(e["src_id"]), int(e["dst_id"])
                        if src == victim:
                            src = target_id
                        if dst == victim:
                            dst = target_id
                        if src == dst:
                            continue
                        cur.execute(
                            """
                            INSERT INTO ka.kg_edges (src_id, dst_id, rel, weight, doc_id)
                            VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (src_id, dst_id, rel) DO NOTHING
                            """,
                            (src, dst, e["rel"], e["weight"], e["doc_id"]),
                        )
                    cur.execute("DELETE FROM ka.kg_nodes WHERE id=%s", (victim,))
                    by_type_key.pop((ntype, nkey), None)
                    merged += 1

                conn.commit()
        return merged
    except Exception as e:  # noqa: BLE001
        logger.warning("PG kg entity resolve failed: %s", e)
        return 0


def pg_graph_expand(hit_ids: list[int], hit_urls: list[str]) -> list[int]:
    """Graph expansion via ka.kg_edges: documents connected to winner documents
    by a graph edge (RELATES_TO / DISCUSSES / …) become candidate neighbours.

    Returns new doc ids (PG content ids, not the winners themselves), capped at 2.
    """
    winners = {int(x) for x in hit_ids if str(x).lstrip("-").isdigit()}
    if not winners:
        return []
    wkeys = [f"doc:{x}" for x in winners]
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                marks = ",".join(["%s"] * len(wkeys))
                cur.execute(
                    "SELECT id, key FROM ka.kg_nodes "
                    f"WHERE type='document' AND key IN ({marks})",
                    wkeys,
                )
                winner_nodes = {int(r["id"]) for r in cur.fetchall()}
                if not winner_nodes:
                    return []

                marks2 = ",".join(["%s"] * len(winner_nodes))
                cur.execute(
                    "SELECT src_id, dst_id FROM ka.kg_edges "
                    f"WHERE src_id IN ({marks2}) OR dst_id IN ({marks2})",
                    list(winner_nodes) + list(winner_nodes),
                )
                related = set()
                for r in cur.fetchall():
                    src, dst = int(r["src_id"]), int(r["dst_id"])
                    if src in winner_nodes:
                        related.add(dst)
                    elif dst in winner_nodes:
                        related.add(src)
                related -= winner_nodes
                if not related:
                    return []

                marks3 = ",".join(["%s"] * len(related))
                cur.execute(
                    f"SELECT key FROM ka.kg_nodes WHERE id IN ({marks3})",
                    list(related),
                )
                out: list[int] = []
                for r in cur.fetchall():
                    k = r["key"]
                    if k.startswith("doc:"):
                        try:
                            cid = int(k[4:])
                            if cid not in winners:
                                out.append(cid)
                        except ValueError:
                            pass
                return list(dict.fromkeys(out))[:2]
    except Exception as e:  # noqa: BLE001
        logger.warning("PG graph expand failed: %s", e)
        return []
