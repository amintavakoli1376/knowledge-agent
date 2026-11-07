# گزارش اپیک ۲ — Odoo Mirror (JSON-2 Client)

**برنچ:** `epic2/odoo-mirror`
**تاریخ:** 2026-11-07
**پلن:** `.hermes/plans/2026-11-07_epic2-odoo-mirror.md` (۱۳ تسک، تأییدشده)

---

## وضعیت کلی — ✅ کامل شد

| فاز | محتوا | وضعیت |
|---|---|---|
| ۲A | زیرساخت + کلاینت JSON-2 | ✅ کامل (A1–A3) |
| ۲B | آینه اصلی (save/والد/dedup) | ✅ کامل (B1–B4) |
| ۲C | مقاوم‌سازی (Retry/DLQ/Breaker/RateLimit) | ✅ کامل (C1–C4) |
| ۲D | Structured Logging | ✅ کامل (D1) |
| ۲E | اتصال به main.py + تست یکپارچگی | ✅ کامل (E1–E2) |

**تست‌ها:** 124 پاس + 1 skip (از 88 اولیه به 124 نهایی)

---

## فاز A — زیرساخت + کلاینت (A1–A3)

- **A1** — `src/config.py`: فیلدهای `odoo_enabled` / `odoo_url` / `odoo_db` / `odoo_api_key` / `odoo_uid` + Validator (اگر enabled ولی ناقص → `ValidationError`)
- **A2** — `src/storage/odoo_client.py`: کلاینت JSON-RPC 2.0 با `execute_kw(model, method, args, kwargs)` (فرم ۷-آرگومانی)، هدرهای `X-Odoo-Database` + `API-KEY`، بلاک urllib ایمن (timeout 30s)
- **A3** — `_is_retryable()`: 429/5xx/timeout → بله؛ 401/403/4xx → نه. `_extract_error()` بدون افشای API key. Exceptionها: `AuthError` / `RateLimitError` / `OdooError`

---

## فاز B — آینه اصلی (B1–B4)

- **B1** — `src/storage/odoo.py`: `OdooStorage.save(content, analysis) -> str` (الگوی outline)
- **B2** — `get_or_create_parent` بر اساس platform
- **B3** — ساده‌شده (تصمیم کاربر): به‌جای `article_properties` از فیلدهای بومی `article_url` + `name` + `body` + `parent_id`
- **B4** — جدول sqlite `odoo_articles` برای dedup (mirror_ids)
- تست: `tests/test_odoo_storage.py` (FakeClient)

---

## فاز C — مقاوم‌سازی (C1–C4)

- **C1** — `_with_retry()` در `odoo_client.py`: Retry Exponential Backoff (پایه‌افزایی + jitter، فقط روی خطاهای retryable)
- **C2** — `src/storage/circuit_breaker.py`: کلاس `CircuitBreaker` (CLOSED → OPEN بعد از N شکست متوالی → HALF_OPEN بعد از cooldown → یک درخواست آزمایشی)
- **C3** — `src/storage/dlq.py`: جدول sqlite `odoo_dlq` (source_url/payload/error/attempts) + ادغام در `OdooStorage.save()` (در شکست → enqueue)
- **C4** — `src/storage/rate_limiter.py`: کلاس `TokenBucket`
- تست: `tests/test_circuit_breaker.py`، `tests/test_rate_limiter.py`، `tests/test_dlq.py`

---

## فاز D — Structured Logging (D1)

- `src/storage/logging_util.py`: `get_structured_logger()` + `JsonFormatter` — خروجی JSON-line
- ماسک خودکار کلیدها (API key/password/token + الگوهای ۴۰-hex و sk-/pk-)
- `odoo.py` و `odoo_client.py` به logger ساخت‌یافته مهاجرت کردند
- تست: `tests/test_logging_util.py` (۳ تست)

---

## فاز E — اتصال + تست یکپارچگی (E1–E2)

- **E1** — `src/main.py` `api_save`: بلوک «Step 3.6» با `if settings.odoo_enabled` → `OdooStorage().save()` در `asyncio.to_thread` (best-effort، non-fatal، مانند Outline). `SaveResponse` + `odoo_url`
- **E2** — `tests/test_odoo_integration.py`: تست زنده روی `ka_test` (پارس دستی `.env.test`، مارکر `integration`)
- تست اتصال: `tests/test_odoo_wiring.py` (وقتی enabled → صدا زده می‌شود؛ وقتی disabled → skip)

---

## اعتبارنامه و دسترسی Odoo (ka_test)

- URL: `http://127.0.0.1:8069` (کانتینر `odoo-app`)، دیتابیس `ka_test`
- احراز: `uid=6` (کاربر `knowledge-agent-test@local`) + کلید سرویس [REDACTED] به‌عنوان رمز — فرم ۷-آرگومانی `execute_kw`
- مدل `knowledge.article`: ۳۳ رکورد؛ فیلدها: name/body/article_url/category/parent_id
- `res.partner`: count = 3

---

## Commitها

| commit | شرح |
|---|---|
| `6fef1aa` | feat(config): Odoo 19 mirror settings (enabled/url/db/api_key) + validator |
| `b32bb0f` | feat(storage): OdooClient JSON-RPC 2.0 + error handling (auth/429/jsonrpc, retryable) |
| `01d838f` | test(storage): A3 — retryable classification + JSON-RPC error extraction |
| `33c496e` | feat(storage): OdooClient auth via uid+key (7-arg execute_kw) + live ka_test integration test |
| `b297873` | feat(storage): OdooStorage mirror save() — parent get-or-create, article_url/body, sqlite mirror dedup (B1-B4) |
| `5131e54` | feat(storage): resilience — TokenBucket, CircuitBreaker, DLQ, exponential backoff (C1-C4) |
| `cf2109b` | feat(storage): structured JSON logging with secret redaction (D1) |
| `10b1bf7` | feat(main): wire OdooStorage mirror into api_save (E1) |

---

## نکات

- **Attachment (ir.attachment)**: طبق تصمیم کاربر از اپیک حذف شد (YAGNI — هیچ داده باینری در pipeline نیست) و به Backlog جداگانه منتقل شد
- **Deploy**: ممنوع — فقط push به گیت؛ CI بعداً روی GitHub Actions
- **فایل موقت** `/tmp/odoo_new_key.txt` باید پاک شود
