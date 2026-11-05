# Task 5 — آماده‌سازی دیتابیس تست محلی

**اپیک:** Discovery و Validation فنی (Spike) · **فاز:** phase-1 · **SP:** 2
**تاریخ:** 2026-11-05 · **وضعیت:** انجام شد (دیتابیس تست + پیکربندی آماده)
**جایگزین:** «Staging branch در Odoo.sh» — که روی self-hosted معنا ندارد.

---

## ۱. چه چیزی ساخته شد

| مورد | شناسه / مقدار |
|---|---|
| دیتابیس تست | `ka_test` (همان کلاستر `odoo-db`، `postgres:16`) |
| ماژول‌های نصب‌شده | `base` + `knowledge` (بدون demo data) |
| جداول ساخته‌شده | ۲۴۰ جدول در schema `public` |
| کاربر سرویس تست | `knowledge-agent-test@local` (`res.users` id=6، فقط Internal User) |
| Workspace ریشه تست | «Knowledge Agent Test» (`knowledge.article` id=36، `internal_permission='read'`) |
| عضویت | کاربر تست با `permission='write'` (inline در همان `create`، الگوی T2) |
| کلید API | scope `rpc`، انقضا ۹۰ روز → `/root/odoo/.ka_test_key` (`chmod 600`) |

**نکتهٔ مهم:** کاربر و Workspace در `ka_test` ساخته شدند، نه در `production`. با کوئری مستقیم
`psql -d ka_test` تأیید شد (کاربر id=6 و مقالهٔ id=36 آنجا هستند) و `production` دست‌نخورده ماند
(همان ۲ کاربر و ۳۴ مقالهٔ قبلی).

---

## ۲. یافته‌های فنی اندازه‌گیری‌شده

### الف) JSON-2 با چند دیتابیس به هدر `X-Odoo-Database` نیاز دارد
وقتی فقط `production` وجود داشت، `POST /json/2/<model>/<method>` بدون هیچ اشاره‌ای به دیتابیس
کار می‌کرد. بعد از ساخت `ka_test`، همان درخواست با خطای زیر برگشت:

```
404 Not Found — "No database is selected and the requested URL was not found"
```

با افزودن هدر `X-Odoo-Database: ka_test` پاسخ **HTTP 200** شد. منبع تأییدکننده در کد Odoo:
`odoo/http.py:1792` → `header_dbname = self.httprequest.headers.get('X-Odoo-Database')`.

**اثر روی پیاده‌سازی (اپیک ۲):** `OdooStorage` باید هدر `X-Odoo-Database` را همیشه بفرستد
(مقدارش از `ODOO_DB` بیاید). این وابسته به «چند دیتابیس بودن» است، نه به نسخه — پس همیشه
فرستادنش امن‌ترین حالت است.

### ب) کلید API به دیتابیس مقید است
همان کلید `ka_test` روی `production` → **HTTP 401 Invalid apikey**. یعنی کلیدها بین دیتابیس‌ها
مشترک نیستند و هر محیط کلید خودش را لازم دارد (در `.env.test` همین ثبت شد).

### ج) دیتابیس تست خودش دادهٔ پیش‌فرض دارد
پس از نصب `knowledge`، دیتابیس `ka_test` با ۳۳ مقالهٔ پیش‌فرض/خوش‌آمد شروع می‌شود (۳۴ مقاله پس از
ساخت Workspace، با ۱۸ ریشه). پس تست‌های آینه نباید «تعداد کل مقالات» را ملاک بگیرند؛ باید فقط
زیردرخت Workspace ریشه (id=36) را بشمارند.

### د) دسترسی حداقلی، دقیقاً مثل production
کلید تست `create` را مجاز کرد (**200 `[37]`**) ولی `unlink` را رد کرد (**403**، فقط Role/Administrator).
این رفتار تأیید کرد که تنظیمات T3 عمدی و پایدار است. مقالهٔ probe (id=37) بعد از تست از طریق
`odoo shell` پاک شد؛ شمارش به ۳۴ برگشت.

---

## ۳. فایل‌های پیکربندی

| فایل | وضعیت | محتوا |
|---|---|---|
| `.env.test` | **کامیت نمی‌شود** (در `.gitignore`) | اتصال واقعی: `ODOO_URL`, `ODOO_DB=ka_test`, `ODOO_API_KEY`, `ODOO_WORKSPACE_ROOT_ID=36`, `DATA_DIR=data-test` |
| `.env.test.example` | کامیت می‌شود | همان کلیدها با مقدار جای‌نگهدار، تا هر کسی محیط تست خودش را بسازد |

**چرا `DATA_DIR` جدا؟** تا تست‌ها روی SQLite واقعی (`data/knowledge.db`) اثر نگذارند و دادهٔ واقعی
هرگز آلوده نشود.

**بدهی باقی‌مانده (اپیک ۲، نه T5):** `src/config.py` هنوز فیلدهای `odoo_*` را ندارد و `env_file`
روی `.env` قفل است. برای استفاده از `.env.test`، پیاده‌سازی باید یا `env_file` را قابل‌تنظیم کند
(مثلاً `ENV_FILE=.env.test`) یا متغیرهای محیطی را مستقیم بخواند. این در تسک افزودن `OdooStorage`
انجام می‌شود.

---

## ۴. بازتولید (دستورهای دقیق)

```bash
# 1) ساخت دیتابیس
docker exec odoo-db createdb -U odoo -E UTF8 -T template0 ka_test

# 2) نصب ماژول‌ها (بدون demo)
docker exec odoo-app odoo -d ka_test -i base,knowledge --without-demo --stop-after-init

# 3) کاربر سرویس + Workspace + کلید (اسکریپت T5)
#    خروجی: WORKSPACE_ID=<id> و KEY=<کلید>
docker exec odoo-app bash -c "odoo shell -d ka_test --no-http < t5_setup.py"

# 4) تست JSON-2 (هدر دیتابیس الزامی است)
curl -s -X POST http://127.0.0.1:8069/json/2/knowledge.article/search_read \
  -H "Authorization: Bearer <KEY>" \
  -H "X-Odoo-Database: ka_test" \
  -H "Content-Type: application/json" \
  -d '{"domain":[["id","=",36]],"fields":["id","name"],"limit":1}'
```

---

## ۵. تله‌های Odoo 19 که در این تسک برخوردیم

1. **`groups_id` → `group_ids`**: در Odoo 19 نام فیلد گروه‌های کاربر جمع با `ids` است. نوشتن
   `groups_id` خطای `ValueError: Invalid field` می‌دهد.
2. **مقالهٔ `read`/invite-only بدون عضو write در `create` رد می‌شود** — همان یافتهٔ T2؛ راه‌حل
   `article_member_ids: [[0,0,{partner_id, permission:"write"}]]` در همان `create`.
3. **`name` روی `knowledge.article` در این نسخهٔ نصب‌شده `varchar` است، نه `jsonb`** — کوئری
   `name->>'en_US'` خطای «operator does not exist» می‌دهد. برای خواندن مستقیم از psql، از
   `left(name,40)` استفاده کنید.
4. **`--without-demo=all` در Odoo 19 منسوخ است**: لاگ می‌گوید `invalid boolean value: 'all',
   assume True`. یعنی مقدار `all` پذیرفته نمی‌شود و فقط `--without-demo` (بدون مقدار) درست است.
   در دستور بازتولید همین شکل اصلاح شد.
