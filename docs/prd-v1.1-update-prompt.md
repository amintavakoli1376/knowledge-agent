# پرامپت ویرایش PRD — نسخه ۱.۰ به ۱.۱

این متن را کامل کپی کن و به مدل دیگر بده.

---

## پرامپت

تو یک Senior Solution Architect هستی. یک PRD فارسی برای «اتصال Knowledge Agent به Odoo 19 Enterprise (ماژول Knowledge)» در نسخه ۱.۰ نوشته شده که بخشی از فرض‌هایش با واقعیت کدبیس و سرور **نمی‌خواند**. وظیفه‌ات تولید **نسخه ۱.۱ اصلاح‌شده** است، نه بازنویسی از صفر: ساختار، شماره‌گذاری FR/NFR و لحن سند را حفظ کن و فقط جاهایی که در ادامه می‌گویم اصلاح کن.

### قواعد کاری (مهم)
1. هر جا واقعیت را نمی‌دانی، **فرض نساز**. آن را در بخش «Open Questions» بنویس.
2. شماره‌های `FR-xxx` و `NFR-xxx` را تغییر نده؛ اگر موردی حذف می‌شود، شماره‌اش را با وضعیت `Dropped` نگه دار و دلیل بنویس.
3. هیچ فیلد، ماژول، یا endpoint اختراع نکن. فقط از واقعیت‌های زیر استفاده کن.
4. زبان سند فارسی، اصطلاحات فنی به لاتین و درست (مثلاً «ایمیج» نه «ایماژ»). بدون ایموجی.
5. در ابتدای سند یک بخش `Changelog` بگذار که خلاصهٔ تغییرات ۱.۰ → ۱.۱ را فهرست کند.

### واقعیت‌های قطعی (تأییدشده — خلاف این‌ها را ننویس)

**میزبانی و نسخه**
- Odoo 19 Enterprise **self-hosted** روی VPS است (`141.11.175.39:8069`)، نه Odoo.sh. پس محدودیت «External API فقط روی پلن Custom» که مخصوص Odoo.sh/SaaS است، اینجا وجود ندارد.
- ماژول `knowledge` نصب است. ایمیج کانتینر با نسخهٔ بستهٔ enterprise هم‌نسخه شده: `odoo:19.0-20250918`.
- `POST /json/2/knowledge.article/search_read` بدون کلید → `401` (یعنی endpoint وجود دارد). `/jsonrpc` قدیمی هم پاسخ می‌دهد ولی deprecated است؛ هدف باید JSON-2 باشد.

**فیلدهای واقعی `knowledge.article`** (از `ir_model_fields` استخراج شده)
- موجود و قابل نوشتن: `name` (Char)، `body` (Html)، `icon` (Char/Emoji)، `parent_id` (Many2one)، `internal_permission` (Selection)، `article_properties` (Properties، ذخیره‌شده)، `article_properties_definition`، `cover_image_id`، `full_width`.
- `article_url` وجود دارد ولی `store=false` و `readonly` است → **برای Dedup قابل استفاده نیست**.
- فیلد `is_article_visible_by_default` **وجود ندارد**؛ معادل‌های واقعی: `internal_permission`، `is_article_visible_by_everyone`، `inherited_permission`.
- هیچ فیلد `x_*` سفارشی روی مدل تعریف نشده است.
- مجوزدهی Knowledge مدل خودش را دارد: `knowledge.article.member`، `internal_permission`، `inherited_permission`. صرفِ داشتن دسترسی Create/Write روی مدل کافی نیست؛ اگر کاربر سرویس عضو Workspace نباشد مقاله ساخته می‌شود ولی دیده نمی‌شود.

**ساختار واقعی ریپو** (`/root/knowledge-agent-repo`)
- مدل‌ها در `src/models/__init__.py` هستند، نه `src/models/schemas.py`. کلاس `KnowledgeItem` **وجود ندارد**.
- دو آبجکت جدا داریم:
  - `ExtractedContent`: `title, full_text, url, platform, author, published_date, metadata`
  - `AIAnalysis`: `summary_fa, summary_en, key_points, category, tags, priority, entities, concepts, connections, contradictions, analysis_status, analysis_error, analysis_attempts`
- امضای واقعی ذخیره‌سازی: `async def save(self, content: ExtractedContent, analysis: AIAnalysis) -> str | None` (در `src/storage/notion.py` و `src/storage/outline.py`).
- خلاصه‌ساز در `src/processors/summarizer.py` است، نه `src/summarizer/summarizer.py`.
- `src/storage/__init__.py` فقط `NotionStorage` را export می‌کند؛ `base.py` و `factory.py` وجود ندارند.
- زیرساخت موجود که باید دوباره استفاده شود: `src/processors/recovery.py` (بازیابی خطا/سهمیه)، `src/render/` (UI وب)، `src/rag/` (chunker، embedder، vector_store، rag_engine)، `src/storage/concepts.py`، `docs/adr/` (پوشه از قبل هست)، `lxml` و `markdown-it-py` در `requirements.txt`، `bleach` موجود نیست.
- `src/storage/outline.py` بهترین الگوی کپی برای `odoo.py` است (ساخت بدنه از content+analysis و بازگشت URL).

**معماری واقعی ذخیره‌سازی (نقطهٔ اصلی اختلاف با ۱.۰)**
- SQLite منبع حقیقت است: `data/knowledge.db` با جدول‌های `content` (ستون `url` **UNIQUE**)، `doc_versions`، `embeddings` (بردار float32 با ابعاد ۳۸۴ از مدل e5-small)، `concepts` (`norm`, `display`, `doc_count`, `doc_ids`).
- Notion، Outline و AFFiNE **آینه‌های انتشار** هستند، نه backend قابل تعویض. Outline با `OUTLINE_ENABLED` روشن می‌شود و خطایش non-fatal است.
- جریان واقعی در `src/main.py`: Extract → `sqlite_store.save_content()` → Summarize → `sqlite_store.save_analysis()` → embedding → Notion → Outline (اختیاری) → پاسخ با `notion_url`/`outline_url`.
- پس سوییچ `STORAGE_BACKEND=notion|odoo|dual` که در ۱.۰ آمده با معماری نمی‌خواند و باید با الگوی «آینهٔ اضافی» جایگزین شود.

**تصمیمات معماری که کاربر تأیید کرده**
- Odoo به‌عنوان **آینهٔ سوم** اضافه می‌شود (`ODOO_ENABLED`)، کنار Outline، با `try/except` غیرمخرب. SQLite دست نمی‌خورد.
- Dedup از SQLite می‌آید (`url UNIQUE`)، نه از کوئری روی Odoo.
- `source_url`، `source_type` و تگ‌ها در `article_properties` نگه داشته می‌شوند → **هیچ ماژول سفارشی Odoo و هیچ Studio لازم نیست**.
- فاز ۲: انتقال بردار و گراف به Postgres با **schema جدا (`ka`)** در همان کلاستر اودو + `pgvector`. نکته: ایمیج فعلی `postgres:16` افزونهٔ `vector` ندارد (فقط `pg_trgm`) و باید به `pgvector/pgvector:pg16` عوض شود. نوشتن مستقیم در جدول‌های اودو ممنوع؛ نوشتن در اودو فقط از JSON-2.
- knowledge graph فاز ۲: دو جدول `kg_nodes(id, type, key, label)` و `kg_edges(src_id, dst_id, rel, weight, doc_id)` با یال‌های `MENTIONS`, `DISCUSSES`, `RELATES_TO`, `CONTRADICTS`, `SUPERSEDES`, `PART_OF`؛ به‌علاوهٔ ادغام مترادف‌ها (entity resolution) و ترکیب با جست‌وجوی برداری به‌صورت vector top-k → یک گام گسترش در گراف → rerank.

### تغییرات لازم به تفکیک بخش

**بخش ۰ (Discovery)**
با ساختار واقعی ریپو بازنویسی شود. ادعای «فایل‌ها قابل مشاهده نبود» حذف شود؛ به‌جایش جدول «فایل → نقش واقعی» بیاید. مسیرهای غلط (`schemas.py`، `src/summarizer/`) اصلاح شوند.

**بخش ۱ (Goals/Non-goals)**
G3 (Storage Abstraction Layer) به «الگوی آینهٔ چندگانه» تغییر کند. Non-goal جدید اضافه شود: «نوشتن مستقیم SQL در جدول‌های اودو».

**بخش ۳ (User Stories)**
USها بمانند، ولی معیار پذیرش US-2 به Dedup از SQLite تغییر کند و US-3 به `parent_id` واقعی اشاره کند.

**بخش ۴ (FRها)**
- FR-001: مسیر `POST /json/2/knowledge.article/create` درست است؛ تأیید شود که کلید از Preferences > Account Security ساخته می‌شود و هدر `Authorization: bearer` است.
- FR-002: جدول نگاشت با فیلدهای واقعی بازنویسی شود (`content.title` → `name`، `analysis.summary_fa`+`summary_en`+`content.full_text` → `body`، `content.url` → `article_properties.source_url`، `content.platform` → `parent_id`، `analysis.tags` → `article_properties.tags`).
- FR-005 (Lazy parent creation): بماند، ولی کش والدها در SQLite باشد نه فایل جانبی.
- FR-006/FR-007 (Dedup): بازنویسی شود به «Dedup محلی از `content.url` UNIQUE + بررسی وجود رکورد آینه در SQLite؛ جست‌وجو در Odoo فقط برای بازیابی id آینه لازم است».
- FR-011 (`STORAGE_BACKEND`): **Dropped** شود و به‌جایش `ODOO_ENABLED` (آینهٔ سوم، الگوی Outline) بیاید.
- FR-012 (Attachment): بماند؛ با `ir.attachment` و `res_model='knowledge.article'` درست است.
- FR-013/FR-015: تگ‌ها و آیکون از طریق `article_properties` و فیلد `icon` — نه درج در بدنه.

**بخش ۵ (NFRها)**
- NFR-002 اصلاح شود: علاوه بر دسترسی مدل، کاربر سرویس باید عضو Workspace مقصد باشد (`knowledge.article.member` / `internal_permission`)؛ در غیر این صورت مقاله ساخته می‌شود ولی دیده نمی‌شود.
- NFR-005 و NFR-007 بمانند؛ پیاده‌سازی روی `processors/recovery.py` سوار شود، نه ماژول جدید.
- NFR-009 (JSON-2) بماند و تأیید شود که روی این سرور فعال است.

**بخش ۶ (معماری)**
بازنویسی کامل: نمودار جریان با SQLite به‌عنوان منبع حقیقت و سه آینه (Notion/Outline/Odoo). جدول مقایسهٔ «سرویس خارجی vs ماژول سفارشی» حفظ شود ولی نتیجه‌اش به «سرویس خارجی + Property Fields، بدون ماژول سفارشی» تغییر کند. بخش ۶.۱ (Interface) به‌صورت اختیاری/فاز ۲ علامت بخورد، چون امروز هیچ interface مشترکی وجود ندارد و اضافه‌کردنش پیش‌نیاز MVP نیست.

**بخش ۷ (Field Mapping)**
با فیلدهای واقعی بازنویسی شود. ستون‌های جعلی حذف شوند. یادداشت صریح بیاید که `article_url` برای Dedup قابل استفاده نیست.

**بخش ۸ (تغییرات فایل‌ها)**
مسیرها اصلاح شوند: `src/models/__init__.py`، `src/processors/summarizer.py`، و `src/storage/odoo.py` به‌عنوان فایل جدید با امضای `save(content, analysis)`. `factory.py` و `base.py` به فاز ۲ منتقل شوند. `docs/adr/` از قبل موجود است.

**بخش ۹ (متغیرهای محیطی)**
`STORAGE_BACKEND` و `ODOO_DEDUP_FIELD` حذف شوند. به‌جایشان `ODOO_ENABLED`، `ODOO_URL`، `ODOO_API_KEY`، `ODOO_WORKSPACE_ROOT_ID`، `ODOO_PARENT_*_ID`، `ODOO_TIMEOUT_SECONDS`، `ODOO_MAX_RETRIES` بمانند/اضافه شوند.

**بخش ۱۰ (خطا و Edge Cases)**
DLQ روی `processors/recovery.py` و SQLite سوار شود. سناریوی «کاربر سرویس عضو Workspace نیست» اضافه شود. سناریوی «Odoo در دسترس نیست» به الگوی غیرمخرب Outline تغییر کند (مسیر اصلی نباید بشکند).

**بخش ۱۲ (Rollout)**
فاز ۰: ساخت API Key و تست `curl` روی همین سرور self-hosted (بدون Staging branch اودو.ش؛ به‌جایش یک دیتابیس تست محلی یا کپی از `production`).
فاز ۱ (MVP): فقط آینهٔ اودو + SQLite دست‌نخورده.
فاز ۲: مهاجرت بردار و گراف به schema `ka` + `pgvector` + knowledge graph.
فاز ۳: حذف تدریجی Notion در صورت تصمیم کاربر.

**بخش ۱۳ (ریسک‌ها)**
ریسک «پلن Custom» حذف شود. ریسک‌های جدید: «آپدیت اودو روی همان کلاستر پستگرس»، «تغییر ایمیج پستگرس برای pgvector»، «مجوزدهی Workspace».

**بخش ۱۵ (Open Questions)**
- Q1 (فیلد Dedup): **پاسخ داده شد** → `article_properties`، بدون ماژول سفارشی.
- Q2 (میزبانی): **پاسخ داده شد** → self-hosted.
- Q3 (پلن): **منتفی** → محدودیت پلن روی self-hosted وجود ندارد.
- Q4 تا Q8 (استراتژی Notion، سطح دسترسی پیش‌فرض، اولویت جستجو، تگ‌گذاری، محدودهٔ Migration) باز بمانند.
- سؤال باز جدید: «آیا در فاز ۲ مهاجرت کامل SQLite → Postgres انجام شود یا دو منبع موازی بمانند؟»

### خروجی مورد انتظار
کل سند نسخه ۱.۱ به‌صورت Markdown کامل، با `Changelog` در ابتدا، بدون placeholder و بدون «فرض» های بی‌پاسخ (هر ابهام فقط در Open Questions). در پایان سند یک جدول کوتاه بیاور: «چه چیزی از ۱.۰ حذف شد، چه چیزی اصلاح شد، چه چیزی اضافه شد».
