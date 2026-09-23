"""ریکاوری خودکار رکوردهای pending_resummarize — مقاوم‌سازی 429-quota (t_c270e218).

وقتی سهمیهٔ AI (429 «Monthly spending limit») تمام می‌شود، رکورد بدون خلاصه و با
flag ذخیره می‌شود؛ این ماژول بعداً (پس از شارژ سهمیه) خلاصه‌سازی را دوباره تلاش
می‌کند و در موفقیت، خلاصه‌ها را در SQLite و Notion تکمیل می‌کند.

اجرای خودکار: در startup سرویس (lifespan) — watchdog هم سرویس را restart می‌کند
پس این کار تقریباً روزانه فرصت اجرا می‌گیرد.
اجرای دستی/Cron:  python -m src.processors.recovery
"""
import asyncio
import json
import logging
import sys

from ..config import settings
from ..models import ExtractedContent
from ..storage import sqlite_store
from .summarizer import (
    MAX_RESUMMARIZE_ATTEMPTS,
    RECOVERY_SLEEP_SECONDS,
    ContentSummarizer,
    QuotaExceededError,
    is_quota_error,
)

logger = logging.getLogger(__name__)


def _row_to_content(row: dict) -> ExtractedContent:
    """بازسازی ExtractedContent از ردیف DB (برای فراخوانی مجدد خلاصه‌ساز).

    نکته: رکوردهای pending_resummarize ممکن است متن خام یا خلاصهٔ ذخیره‌شده
    (متن پیش از خلاصه‌سازی) داشته باشند — هر چه در full_text باشد برای
    خلاصه‌سازی استفاده می‌شود.
    """
    full_text = row.get("full_text") or ""
    title = row.get("title") or ""
    # تیتر رکورد فوروارد تلگرام خودش ابتدای متن است — جداکردن متن اصلی
    body = full_text
    if title and full_text.startswith(title):
        body = full_text[len(title):].lstrip()
    if not body.strip():
        body = full_text  # متن ناقص هم بهتر از هیچ — رکوردهای با متن خالی skip می‌شوند
    return ExtractedContent(
        url=row.get("url") or "",
        title=title or "Untitled",
        full_text=body,
        platform=row.get("platform") or "website",
    )


async def recover_pending(
    summarizer: ContentSummarizer,
    max_attempts: int = MAX_RESUMMARIZE_ATTEMPTS,
    batch_limit: int = 10,
    storage=None,
    sleep_seconds: float = RECOVERY_SLEEP_SECONDS,
) -> dict:
    """تلاش مجدد خلاصه‌سازی همهٔ رکوردهای pending_resummarize.

    Returns: {"recovered": [...], "failed": [...], "skipped": [...]}
    هر failure شمارندهٔ attempts را بالا می‌برد (سقف max_attempts).
    موفقیت: خلاصه در SQLite + (اگر سند Notion موجود باشد) patch خلاصه.
    """
    rows = sqlite_store.get_pending_resummarize(max_attempts=max_attempts, limit=batch_limit)
    result = {"recovered": [], "failed": [], "skipped": []}

    for row in rows:
        url = row["url"]
        attempts = int(row.get("analysis_attempts") or 0)
        if not (row.get("full_text") or "").strip():
            # متن قابل خلاصه‌سازی نیست — flag پاک می‌شود تا در حلقه نیفتد
            logger.warning(f"Recovery skip (empty full_text): {url}")
            sqlite_store.clear_analysis_status(url)
            result["skipped"].append(url)
            continue

        content = _row_to_content(row)
        try:
            analysis = await summarizer.summarize_ai(content)
        except QuotaExceededError as e:
            # هنوز سهمیه نیست — بقیهٔ batch هم بی‌فایده‌اند؛ attempt ثبت و خروج
            attempts += 1
            sqlite_store.mark_analysis_failed(url, f"quota: {e}", attempts)
            result["failed"].append({"url": url, "error": f"quota: {e}"})
            logger.warning(f"Recovery stopped — quota still exhausted (attempts={attempts})")
            return result
        except Exception as e:  # noqa: BLE001
            attempts += 1
            sqlite_store.mark_analysis_failed(url, str(e), attempts)
            result["failed"].append({"url": url, "error": str(e)[:120]})
            logger.warning(f"Recovery attempt failed for {url}: {e}")
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
            continue

        # موفق — ثبت در DB
        sqlite_store.mark_analysis_complete(url, analysis)

        # patch Notion (best-effort — سند ممکن است وجود نداشته باشد)
        if storage is not None:
            try:
                page_id = await storage.find_page_by_url(url)
                if page_id:
                    ok = await storage.patch_summary(page_id, analysis)
                    logger.info(f"Notion patch {'ok' if ok else 'failed'} for {url}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Notion patch skipped for {url}: {e}")

        result["recovered"].append(url)
        logger.info(f"Recovered summary for {url}")
        if sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)

    return result


async def recover_pending_safe(storage=None) -> dict:
    """نسخهٔ ضدخطا برای فراخوانی در startup — هرگز raise نمی‌کند."""
    try:
        if not settings.openai_api_key:
            logger.info("Recovery skipped: OPENAI_API_KEY not set")
            return {"recovered": [], "failed": [], "skipped": []}
        summarizer = ContentSummarizer()
        return await recover_pending(summarizer, storage=storage)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Recovery crashed (non-fatal): {e}")
        return {"recovered": [], "failed": [], "skipped": []}


def main() -> int:
    """CLI: python -m src.processors.recovery — یک پاس ریکاوری کامل."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from ..storage.notion import NotionStorage

    storage = NotionStorage()
    summary = asyncio.run(recover_pending_safe(storage=storage))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    # اجرای مستقیم: python -m src.processors.recovery (از ریشهٔ ریپو)
    sys.exit(main())
