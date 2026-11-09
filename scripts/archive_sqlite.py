#!/usr/bin/env python
"""Archive the SQLite knowledge DB (Epic 3 retirement step).

After cutover the app reads from Postgres schema `ka`; the SQLite file
remains only as a one-week mirror. This script snapshots it to
/root/backups/sqlite_archive/ with a timestamp, so the working file can
be retired without losing the safety net.

Usage:  ./venv/bin/python scripts/archive_sqlite.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.sqlite_store import DB_PATH

ARCHIVE_DIR = Path("/root/backups/sqlite_archive")


def main() -> int:
    if not DB_PATH.exists():
        print(f"no sqlite db at {DB_PATH} — nothing to archive")
        return 1
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"knowledge.db.{stamp}.bak"
    shutil.copy2(DB_PATH, dest)
    size = dest.stat().st_size
    print(f"archived {DB_PATH} -> {dest} ({size} bytes)")
    print("sqlite is now retired as source of truth; PG schema ka is primary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
