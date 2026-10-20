"""اتصال SQLite با تنظیمات ایمن برای هم‌زمانی (بات + وب روی یک فایل).

چرا: حالت پیش‌فرض SQLite (`journal_mode=delete`) هنگام نوشتن، خواننده‌ها را بلاک
می‌کند و با خطای «database is locked» می‌خورد. با `WAL`:
  - خواندن و نوشتن هم‌زمان ممکن می‌شود (نویسنده، خواننده را بلاک نمی‌کند)
  - نوشتن اتمیک است و اگر پروسه وسط کار بمیرد، فایل سالم می‌ماند
  - `synchronous=NORMAL` سرعت نوشتن را بالا می‌برد (با WAL امن است)
`busy_timeout` هم به‌جای خطای فوری، تا ۱۰ ثانیه صبر می‌کند تا قفل آزاد شود.

`journal_mode` روی خود فایل ذخیره می‌شود (یک‌بار ست شود کافی است) ولی برای
اطمینان در هر اتصال هم اعمال می‌شود؛ روی اتصال read-only اجرا نمی‌شود.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 10_000


def connect_db(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """اتصال استاندارد پروژه: WAL + busy_timeout + row_factory."""
    p = Path(path)
    if readonly:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
        con.row_factory = sqlite3.Row
        return con

    con = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        # فایل روی FS بدون پشتیبانی WAL یا DB قفل‌شده — کار با حالت قبل ادامه می‌دهد
        pass
    return con


def enable_wal(path: str | Path) -> str:
    """اعمال WAL روی فایل دیتابیس (persistent) و برگرداندن حالت نهایی."""
    con = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        con.execute("PRAGMA wal_autocheckpoint=1000")
        return str(mode)
    finally:
        con.close()
