# Task 1 — بررسی ساختار واقعی ریپو و بستن Discovery

**Epic:** Discovery و Validation فنی (Spike) · **فاز:** phase-1 · **SP:** 2
**تاریخ:** 2026-11-04 · **وضعیت:** Done (Discovery)
**جایگزین تسک قدیمی:** «Validate schemas.py» — آن فایل در ریپو وجود ندارد.

هدف: تثبیت «وضعیت واقعی کد» به‌عنوان مبنای طراحی آینهٔ Odoo، تا تسک‌های بعدی روی فرض‌های خیالی بنا نشوند.
همهٔ موارد زیر از خود فایل‌ها/سرویس زنده خوانده شده‌اند (نه از حافظه یا PRD قبلی).

---

## ۱. مدل‌های داده — `src/models/__init__.py` (۷۱ خط)

| کلاس | فیلدها | نکته |
|---|---|---|
| `ExtractedContent` | `title="Untitled"`, `full_text=""`, `url=""`, `platform="website"`, `author=None`, `published_date=None`, `metadata={}` | ۷ فیلد؛ همهٔ مقادیر پیش‌فرض دارند |
| `AIAnalysis` | `summary_fa`, `summary_en`, `key_points[]`, `category="General"`, `tags[]`, `priority="Medium"`, `entities[]`, `concepts[]`, `connections[]`, `contradictions[]`, `analysis_status=""`, `analysis_error=""`, `analysis_attempts=0` | ۱۳ فیلد + پراپرتی `is_pending_resummarize` |
| `SaveRequest` | `url`, `platform?`, `note?` | ورودی `POST /api/save` |
| `SaveResponse` | `success`, `notion_url`, `affine_url`, `outline_url`, `title`, `platform`, `category`, `error` | **باید `odoo_url` به آن اضافه شود** (بخش ۴) |
| `SetupRequest` / `SetupResponse` | — | **مرده**: هیچ‌جا import نمی‌شوند؛ `POST /api/setup` در پاکسازی امنیتی حذف شد |

نتیجهٔ طراحی: آینهٔ Odoo هیچ مدل جدیدی لازم ندارد و باید مستقیماً از همین دو آبجکت تغذیه شود.
`metadata: dict` جای طبیعی «متادیتای مخصوص Odoo» (id مقالهٔ آینه) است، ولی منبع Dedup نیست — به بخش ۲ نگاه کنید.

## ۲. جریان واقعی ذخیره (خوانده‌شده از `src/main.py` و `src/gateway/telegram_bot.py`)

```
validate_public_url → Extract → sqlite_store.save_content()        # Step 1.5
→ summarizer.analyze() (QuotaExceededError → _quota_pending_analysis)
→ sqlite_store.save_analysis(content.url, analysis)                # Step 2.5
→ embedding غیرهمزمان (bot._embed_content)
→ Notion   (await storage.save)                                    # Step 3
→ Outline  (if settings.outline_enabled → asyncio.to_thread)       # Step 3.5
→ SaveResponse / پیام تلگرام
```

**نقاط تماس (call sites) که آینهٔ Odoo باید در همه‌شان اضافه شود — ۵ نقطه:**

| فایل | خط (تقریبی) | نقش |
|---|---|---|
| `src/main.py` | ۲۷۶–۲۹۱ | مسیر API (`POST /api/save`) |
| `src/gateway/telegram_bot.py` | ~۲۹۸–۳۰۷ | مسیر لینک تازه |
| `src/gateway/telegram_bot.py` | ~۴۴۲–۴۵۰ | مسیر دوم (پست/متن) |
| `src/gateway/telegram_bot.py` | ~۴۸۶–۴۹۴ | مسیر سوم (یادداشت با url مصنوعی `note://`) |
| `src/gateway/telegram_bot.py` | ~۲۶۵–۲۶۷ | شاخهٔ لینک تکراری (فعلاً از Notion صرف‌نظر می‌کند) |

الگوی دقیق هر نقطه: `if settings.<mirror>_enabled:` + `try/except Exception` **غیرمخرب** + لاگ `warning`؛ خطای آینه هرگز جریان ذخیره را نمی‌شکند.

## ۳. دو الگوی آینه در ریپو (کپی‌برداری برای `storage/odoo.py`)

| ویژگی | `storage/outline.py` (۲۲۲ خط) | `storage/notion.py` (۳۴۱ خط) |
|---|---|---|
| امضا | `def save(content, analysis) -> str` (sync) | `async def save(content, analysis) -> Optional[str]` |
| فراخوانی | `asyncio.to_thread(OutlineStorage().save, …)` | `await storage.save(…)` |
| HTTP | `urllib.request` + `ssl._create_unverified_context()` | `httpx.AsyncClient(timeout=15)` |
| گیت فعال‌سازی | `if not settings.outline_enabled: raise RuntimeError` | `if not self.api_key: return None` (بدون استثنا) |
| خطا | `RuntimeError` با بدنهٔ پاسخ | `Exception(f"Notion API error ({code})")` |
| Dedup | از طریق `render.registry.get_doc_id(url)` — idempotent | ندارد |
| خروجی | URL عمومی کامل | URL پابلیک بدون لاگین (`_build_public_url`) |
| عوارض جانبی | `_append_index_row` + ثبت مفهوم/رجیستری (همه non-fatal) | افزودن ستون‌های flag در صورت 400 |

**الگوی انتخاب‌شده برای Odoo: `outline.py`** — چون (الف) sync است و از مسیر `to_thread` استفاده می‌کند، (ب) بدنه را از `content+analysis` می‌سازد، (ج) گیت `*_ENABLED` دارد. تفاوت‌ها: احراز هویت با API Key (JSON-2)، و Dedup واقعی از SQLite (`content.url UNIQUE`) نه از رجیستری Outline.
`src/storage/__init__.py` فقط `NotionStorage` را export می‌کند و باید `OdooStorage` هم اضافه شود.

## ۴. پیکربندی — `src/config.py` (۶۸ خط)

`Settings(BaseSettings)` با `env_file=".env"`، `extra="ignore"` (پس افزودن کلید جدید boot را نمی‌شکند).
۳۰ کلید اعلام‌شده: `telegram_*`، `notion_*`، `affine_*` (۷)، `outline_*` (۵)، `brightdata_key`، `openai_*` (۴)، `web_auth_token`، `host`/`port`/`log_level`، `data_dir`، `arvan_*` (۵).

یافته‌های مهم:
- کلیدهای Odoo **هنوز اعلام نشده‌اند**: `odoo_enabled`, `odoo_url`, `odoo_db`, `odoo_api_key` (و در صورت نیاز `odoo_parent_<platform>`) — کار تسک‌های بعدی.
- `env_file` نسبت به **CWD** خوانده می‌شود؛ یونیت systemd مقدار `WorkingDirectory=/root/knowledge-agent-repo` دارد، پس `.env` واقعاً بارگذاری می‌شود (این فرض تایید شد، نه حدس).
- کلیدهای `affine_*` عملاً بلااستفاده‌اند: `AFFINE_ENABLED` نیست، و `storage/affine.py` در خط ۲۱ **top-level** `import socketio` دارد در حالی که `python-socketio` نصب نیست → هر import این ماژول بلافاصله `ModuleNotFoundError` می‌دهد.

### وضعیت واقعی سرویس (probe زنده)
```json
{"status":"ok","telegram_bot":true,"notion":false,"openai":true}
```
یعنی در استقرار فعلی **Notion خاموش است** (کلید در `.env` نیست) و Outline هم `OUTLINE_ENABLED` ندارد؛ پس امروز عملاً SQLite تنها مقصد زندهٔ ذخیره است. این بهترین شاهد برای معماری «SQLite منبع حقیقت، آینه‌ها اختیاری» است — و برای Odoo یعنی مسیر ذخیره باید بدون هیچ آینهٔ فعالی هم سالم بماند.
سرور Odoo هم زنده است: کانتینر `odoo-app` (`odoo:19.0-20250918`) روی `0.0.0.0:8069`، دیتابیس `odoo-db` (`postgres:16`).

## ۵. وابستگی‌ها — `requirements.txt` (۲۵ خط) در برابر import واقعی `src/`

اسکن AST روی `src/` (importهای واقعی، بدون re-export) و مقایسه با venv:

| بسته | import در src؟ | در `requirements.txt` | نصب در venv | نتیجه |
|---|---|---|---|---|
| `bleach` | **خیر** | خیر | خیر | **مورد نیاز نیست** — `markdown-it-py` 4.2.0 نصب است و `render/parser.py` با `html=False` کار می‌کند؛ فقط اگر روزی HTML بیرونی را پاک‌سازی کنیم باید اضافه شود |
| `playwright` | بله (`extractors/instagram.py` ×۲) | **خیر** | خیر | import داخل `try/except ImportError` است → fallback بی‌صدا؛ فقط استراتژی ۲/۳ اینستاگرام می‌میرد |
| `youtube_transcript_api` | بله (`extractors/youtube.py`) | **خیر** | خیر | همان‌جا try/except → استخراج یوتیوب بدون زیرنویس |
| `socketio` | بله (`storage/affine.py`) | **خیر** | خیر | **unguarded و top-level** → import ماژول کرش می‌کند |
| `pymupdf`/`fitz` | بله | `pymupdf>=1.26` | 2.0 | کار می‌کند ولی `import fitz` اخطار `FutureWarning: the fitz API is deprecated` می‌دهد |

**برای آینهٔ Odoo هیچ وابستگی جدیدی لازم نیست** (هم `httpx` و هم `urllib` موجودند؛ `markdown-it-py` برای تبدیل Markdown→HTML کافی است).
اصلاح صریح PRD: جملهٔ «`bleach` موجود نیست و باید در صورت نیاز اضافه شود» باید به «`bleach` نه لازم است و نه import شده» تغییر کند؛ و ریسک واقعی وابستگی، سه بستهٔ بالا هستند نه `bleach`.

## ۶. تصمیم‌های قطعی‌شده برای تسک‌های بعدی

1. `storage/odoo.py` هم‌الگوی `outline.py`، sync + `asyncio.to_thread`، گیت `ODOO_ENABLED`.
2. Dedup آینه از `content.url UNIQUE` در SQLite + کش `odoo_article_id` — نه از `article_url` اودو (`store=false`).
3. `odoo_url` به `SaveResponse` و به دکمهٔ تلگرام اضافه شود؛ ۵ نقطهٔ تماس بالا همه باید آینه را صدا بزنند.
4. کلیدهای Odoo به `config.py` با مقدار پیش‌فرض اضافه شوند (`extra="ignore"` پس بی‌خطرند).
5. حذف/بازنشستگی `SetupRequest`/`SetupResponse` و کلیدهای مردهٔ `affine_*` (تسک جداگانه، خارج از دامنهٔ Odoo).
