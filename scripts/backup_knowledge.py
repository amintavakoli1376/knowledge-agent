"""بکاپ knowledge.db روی ArvanCloud Object Storage (S3-compatible).

مراحل:
  ۱) sqlite3 `.backup` (WAL-safe — کپی خام فایل در حالت WAL ناقص می‌شود)
  ۲) gzip + sha256
  ۳) آپلود با rclone (remote موقت از env ساخته می‌شود؛ فایل conf ذخیره نمی‌شود)
  ۴) نگهداری ۷ نسخهٔ روزانه + ۴ هفتگی + ۳ ماهانه (بر اساس تاریخ نام فایل)
  ۵) تست بازیابی: نسخهٔ آپلودشده دانلود و `integrity_check` + تطبیق تعداد اسناد

کلیدها از .env خوانده می‌شوند: ARVAN_ENDPOINT / ARVAN_REGION / ARVAN_ACCESS_KEY /
ARVAN_SECRET_KEY / ARVAN_BUCKET. هیچ‌کدام در گیت نیستند.

اجرا:  ./venv/bin/python scripts/backup_knowledge.py [--dry-run] [--no-verify]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "knowledge.db"
ENV_FILE = REPO / ".env"

KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 3


def load_env() -> dict[str, str]:
    """خواندن .env بدون نیاز به پکیج اضافه؛ env واقعی اولویت دارد."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ARVAN_ENDPOINT", "ARVAN_REGION", "ARVAN_ACCESS_KEY", "ARVAN_SECRET_KEY", "ARVAN_BUCKET"):
        if os.environ.get(k):
            values[k] = os.environ[k]
    return values


def sqlite_backup(dest: Path) -> None:
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)  # WAL-safe
        finally:
            out.close()
    finally:
        src.close()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def doc_count(path: Path) -> int:
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    finally:
        con.close()


def rclone_remote(env: dict[str, str]) -> str:
    """remote موقت: `:s3,provider=ArvanCloud,...:` — بدون نوشتن rclone.conf"""
    return (
        f":s3,provider=ArvanCloud,access_key_id={env['ARVAN_ACCESS_KEY']},"
        f"secret_access_key={env['ARVAN_SECRET_KEY']},"
        f"endpoint={env['ARVAN_ENDPOINT']},region={env['ARVAN_REGION']}:"
        f"{env['ARVAN_BUCKET']}"
    )


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def retention(remote: str, dry: bool) -> list[str]:
    """حذف نسخه‌های اضافی: ۷ روزانه، ۴ هفتگی (شنبه‌ها)، ۳ ماهانه (اول ماه)."""
    listing = run(["rclone", "lsf", remote, "--files-only"])
    if listing.returncode != 0:
        print(f"  ! لیست‌گیری برای retention ناموفق: {listing.stderr.strip()[:200]}")
        return []
    names = sorted(n for n in listing.stdout.split("\n") if n.endswith(".db.gz"))
    parsed = []
    for n in names:
        try:
            stamp = datetime.strptime(n.split("_")[-1].replace(".db.gz", ""), "%Y-%m-%d")
        except ValueError:
            continue
        parsed.append((stamp, n))
    parsed.sort(reverse=True)

    keep: set[str] = set()
    daily = weekly = monthly = 0
    seen_weeks: set[str] = set()
    seen_months: set[str] = set()
    for stamp, name in parsed:
        week = stamp.strftime("%G-%V")
        month = stamp.strftime("%Y-%m")
        if daily < KEEP_DAILY:
            keep.add(name); daily += 1
        if week not in seen_weeks and weekly < KEEP_WEEKLY:
            keep.add(name); seen_weeks.add(week); weekly += 1
        if month not in seen_months and monthly < KEEP_MONTHLY:
            keep.add(name); seen_months.add(month); monthly += 1

    removed = []
    for _stamp, name in parsed:
        if name in keep:
            continue
        if dry:
            print(f"  [dry-run] حذف می‌شد: {name}")
        else:
            r = run(["rclone", "deletefile", f"{remote}/{name}"])
            if r.returncode == 0:
                removed.append(name)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="فقط بساز و آپلود نکن")
    ap.add_argument("--no-verify", action="store_true", help="تست بازیابی انجام نشود")
    args = ap.parse_args()

    env = load_env()
    missing = [k for k in ("ARVAN_ENDPOINT", "ARVAN_REGION", "ARVAN_ACCESS_KEY", "ARVAN_SECRET_KEY", "ARVAN_BUCKET") if not env.get(k)]
    if missing and not args.dry_run:
        print(f"✗ کلیدهای ArvanCloud ناقص است: {', '.join(missing)}")
        print("  در .env اضافه کن: ARVAN_ENDPOINT, ARVAN_REGION, ARVAN_ACCESS_KEY, ARVAN_SECRET_KEY, ARVAN_BUCKET")
        return 2
    if missing:
        print(f"! کلیدها تنظیم نشده‌اند ({', '.join(missing)}) — فقط تست محلی")

    if not DB.exists():
        print(f"✗ دیتابیس پیدا نشد: {DB}")
        return 2

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    tmpdir = Path(tempfile.mkdtemp(prefix="ka-backup-"))
    raw = tmpdir / f"knowledge_{today}_{ts}.db"
    gz = raw.with_suffix(".db.gz")

    print(f"۱) بکاپ WAL-safe از {DB} …")
    sqlite_backup(raw)
    n_docs = doc_count(raw)
    integrity = sqlite3.connect(str(raw)).execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        print(f"✗ integrity_check روی نسخهٔ بکاپ: {integrity}")
        return 3

    print("۲) فشرده‌سازی …")
    with raw.open("rb") as f_in, gzip.open(gz, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    digest = sha256_of(gz)
    size_mb = gz.stat().st_size / 1e6
    print(f"   {gz.name} · {size_mb:.2f} MB · sha256={digest[:16]}…")

    if args.dry_run:
        print(f"[dry-run] فایل آماده است: {gz} (آپلود نشد)")
        return 0

    remote = rclone_remote(env)
    print("۳) آپلود به ArvanCloud …")
    up = run(["rclone", "copyto", str(gz), f"{remote}/{gz.name}", "--s3-chunk-size", "8M"])
    if up.returncode != 0:
        print(f"✗ آپلود ناموفق: {up.stderr.strip()[:300]}")
        return 4
    print(f"   آپلود شد: {gz.name}")

    if not args.no_verify:
        print("۴) تست بازیابی (دانلود + integrity) …")
        check = tmpdir / "verify.db.gz"
        dl = run(["rclone", "copyto", f"{remote}/{gz.name}", str(check)])
        if dl.returncode != 0:
            print(f"✗ دانلود تست ناموفق: {dl.stderr.strip()[:200]}")
            return 5
        if sha256_of(check) != digest:
            print("✗ sha256 نسخهٔ آپلودشده با نسخهٔ محلی یکسان نیست")
            return 6
        vdb = tmpdir / "verify.db"
        with gzip.open(check, "rb") as f_in, vdb.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        vdocs = doc_count(vdb)
        if vdocs != n_docs:
            print(f"✗ تعداد اسناد بازیابی‌شده ({vdocs}) با مبدأ ({n_docs}) فرق دارد")
            return 7
        print(f"   ✓ بازیابی سالم: {vdocs} سند")

    print("۵) نگهداری نسخه‌ها …")
    removed = retention(remote, args.dry_run)
    if removed:
        print(f"   حذف‌شده: {', '.join(removed)}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"✅ بکاپ کامل شد: {gz.name} · {n_docs} سند · {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
