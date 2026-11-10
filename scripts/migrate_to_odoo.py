#!/usr/bin/env python
"""One-time backfill: PG (schema `ka`) -> Odoo mirror (FR-010).

Reads existing content rows from the source-of-truth table ``ka.content``
and pushes each through the *same* live path ``OdooStorage.save()`` used by
the Telegram bot / API, so the mirror stays consistent (parent per platform
under the workspace root, article_properties, attachment threshold, DLQ).

Idempotent: `save()` dedups via the SQLite mirror registry keyed by
``source_url``; rerunning skips already-mirrored rows.

Usage:
  venv/bin/python scripts/migrate_to_odoo.py [--dry-run] [--limit N] [--platform X]

Report counts: total / already_mirrored / created / failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.models import AIAnalysis, ExtractedContent
from src.storage.db_pg import connect_pg
from src.storage.odoo import OdooStorage


def _json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        v = json.loads(value)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _json_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        v = json.loads(value)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _rows(limit: int | None, platform: str | None):
    sql = "SELECT id, url, title, platform, full_text, author, category, " \
          "priority, tags, summary_fa, summary_en, key_points, metadata, " \
          "created_at FROM ka.content"
    conds = []
    args = []
    if platform:
        conds.append("platform = %s")
        args.append(platform)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT %s"
        args.append(int(limit))
    with connect_pg() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                yield dict(zip(cols, row))


def _to_content(row: dict) -> ExtractedContent:
    pub = None
    if row.get("created_at"):
        pub = row["created_at"].strftime("%Y-%m-%d")
    return ExtractedContent(
        title=(row.get("title") or "Untitled"),
        full_text=(row.get("full_text") or ""),
        url=(row.get("url") or "").strip(),
        platform=(row.get("platform") or "website"),
        author=row.get("author"),
        published_date=pub,
        metadata=_json_dict(row.get("metadata")),
    )


def _to_analysis(row: dict) -> AIAnalysis:
    return AIAnalysis(
        summary_fa=(row.get("summary_fa") or ""),
        summary_en=(row.get("summary_en") or ""),
        key_points=_json_list(row.get("key_points")),
        category=(row.get("category") or "General"),
        tags=_json_list(row.get("tags")),
        priority=(row.get("priority") or "Medium"),
    )


def main():
    ap = argparse.ArgumentParser(description="Backfill PG ka.content -> Odoo (FR-010)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--platform", default=None)
    args = ap.parse_args()

    if not settings.odoo_enabled:
        print("ERROR: ODOO_ENABLED=false — refusing to run. Set it in .env first.")
        sys.exit(1)

    store = OdooStorage()

    total = 0
    already = 0
    created = 0
    failed = 0
    failures = []

    for row in _rows(args.limit, args.platform):
        total += 1
        content = _to_content(row)
        url = content.url
        if not url:
            failed += 1
            failures.append((row.get("id"), "<no-url>", "missing url"))
            continue

        was_in_mirror = store._get_mirror(url)

        # dry-run: فقط mirror SQLite را چک کن (بدون شبکه).
        if args.dry_run:
            if was_in_mirror:
                already += 1
            else:
                created += 1  # در dry-run یعنی would_create
            continue

        if was_in_mirror:
            already += 1
            continue

        res = store.save(content, _to_analysis(row))
        if res is None:
            failed += 1
            failures.append((row.get("id"), url, "save returned None"))
        else:
            created += 1

    print(f"backfill summary (rows read): total={total} "
          f"already_mirrored={already} created={created} failed={failed}")
    for cid, u, why in failures[:20]:
        print(f"  FAIL id={cid} {why} {u[:80]}")
    if args.dry_run:
        print("DRY RUN — no changes made.")
    else:
        print("done.")


if __name__ == "__main__":
    main()
