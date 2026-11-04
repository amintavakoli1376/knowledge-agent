# Task 2 — ساخت API Key و تست JSON-2 روی سرور self-hosted

**Epic:** Discovery و Validation فنی (Spike) · **فاز:** phase-1 · **SP:** 2
**تاریخ:** 2026-11-04 · **وضعیت:** Done (تست‌های زنده روی Odoo 19 production)
**جایگزین تسک قدیمی:** «تأیید پلن Custom» (این محدودیت مخصوص Odoo.sh است و برای self-hosted وجود ندارد).

هدف: اثبات عملیِ اینکه JSON-2 روی همین instance کار می‌کند، و ثبت دقیق shape درخواست‌ها
برای پیاده‌سازی `storage/odoo.py` — نه اتکا به مستندات عمومی.

---

## ۱. مشخصات محیط (اندازه‌گیری‌شده)

| مورد | مقدار |
|---|---|
| نسخه/ایمیج | `odoo:19.0-20250918` (کانتینر `odoo-app`, healthy) |
| دیتابیس | `production` (روی کانتینر `odoo-db`، `postgres:16`) |
| Base URL محلی | `http://127.0.0.1:8069` — از بیرون `http://141.11.175.39:8069` |
| ماژول‌ها | `knowledge` **installed** (19.0.1.0) · `base` installed (19.0.1.3) |
| کاربران موجود | فقط `admin` (uid 2, active) — کاربر سرویس هنوز ساخته نشده (کار T3) |
| تعداد مقاله‌های موجود | ۳۲ (`knowledge_article`)، اعضای فعال: ۱ ردیف |
| URL یک مقاله | `http://141.11.175.39:8069/knowledge/article/<id>` |

## ۲. ساخت کلید API

کلید از **داخل خود کانتینر** با `odoo shell` ساخته شد (بدون هیچ رمزی در چت و بدون لاگین مرورگر):

```python
# docker exec odoo-app bash -c "odoo shell -d production --no-http < /tmp/gen_key.py"
user = env["res.users"].browse(2)
key = env["res.users.apikeys"].with_user(user)._generate(
    "rpc", "Knowledge Agent — JSON-2 (T2 smoke test)", datetime.now() + timedelta(days=90))
env.cr.commit()
```

**اهمیت `with_user(user)`:** در Odoo 19 امضای `_generate(self, scope, name, expiration_date)` است و
`user_id` را از **`self.env.user.id`** می‌خواند؛ بدون `with_user` کلید به کاربر اشتباه (superuser) وصل می‌شود.

نتیجهٔ تأییدشده در دیتابیس: `res_users_apikeys` → id=1، `name='Knowledge Agent — JSON-2 (T2 smoke test)'`،
`user_id=2`، `scope='rpc'`، `expiration_date=2027-02-02`.

نگهداری کلید (خارج از چت و خارج از گیت):

- مسیر فایل: `/root/odoo/.ka_api_key` — `chmod 600`، طول ۴۰ کاراکتر hex (`os.urandom(API_KEY_SIZE)`).
- اثر انگشت برای تأیید بدون افشا: `sha256` با پیشوند `4c70d8a3bda57bb5`.
- **بدهی ثبت‌شده:** این کلید برای `admin` (uid 2) است تا JSON-2 اثبات شود؛ در T3 با کلید کاربر سرویس
  جایگزین و کلید موقت باید revoke شود. مقدار کلید در گام پیاده‌سازی به `ODOO_API_KEY` در `.env` منتقل می‌شود.

## ۳. نتایج تست‌های زنده (با کد HTTP واقعی)

| # | تست | درخواست | نتیجه |
|---|---|---|---|
| 1 | بدون کلید | `POST /json/2/knowledge.article/search_read` | **401** `"User not authenticated, use an API Key with a Bearer Authorization header."` |
| 2 | کلید غلط | همان + `Authorization: bearer deadbeef` | **401** `"Invalid apikey"` |
| 3 | کلید درست | `search_read` با `{"domain": [], "fields": ["id","name","internal_permission"], "limit": 5}` | **200** + ۵ ردیف |
| 4 | `create` به شکل غلط | `{"vals": {...}}` | **422** `missing a required argument: 'vals_list'` |
| 5 | `create` درست | `{"vals_list": [{"name": "..."}]}` | **200** → `[34]` |
| 6 | `read` | `{"ids": [34], "fields": [...]}` | **200** + `article_url` برگشت |
| 7 | `write` روی body | `{"ids": [34], "vals": {"body": "<h1>…</h1>"}}` | **200** `true` |
| 8 | `unlink` | `{"ids": [34]}` | **200** `true` — و `search_count` به ۳۲ برگشت (بدون زباله) |

### shape نهایی هر متد (JSON-2)

```http
POST /json/2/<model>/<method>
Authorization: bearer <API_KEY>
Content-Type: application/json
```

| متد | بدنه | خروجی |
|---|---|---|
| `search_read` | `{"domain": [...], "fields": [...], "limit": n, "order": "id desc"}` | لیست دیکشنری |
| `search_count` | `{"domain": [...]}` | عدد |
| `read` | `{"ids": [...], "fields": [...]}` | لیست (id ناموجود → ردیفی از `false`ها، نه لیست خالی) |
| `create` | `{"vals_list": [{...}]}` ← **`vals_list` الزامی است** | لیست idها |
| `write` | `{"ids": [...], "vals": {...}}` | `true` |
| `unlink` | `{"ids": [...]}` | `true` |

نکتهٔ مسیر: `/json/2` نسخهٔ رسمی و مورد پشتیبانی است؛ `/jsonrpc` همچنان جواب می‌دهد ولی deprecated است و استفاده نمی‌شود.

## ۴. یافتهٔ مهم: ساخت مقالهٔ «فقط با دعوت» بدون عضو Write شکست می‌خورد

این یافته، `NFR-002` را **تجربی** اثبات می‌کند (نه فقط بر اساس مستندات):

```
HTTP 422 — odoo.exceptions.ValidationError:
"The article '📄 KA T2 smoke test …' needs at least one member with 'Write' access."
```

- `create` با `{"internal_permission": "none"}` و بدون عضو → **422**.
- راه‌حل تأییدشده در **یک تماس**: ساختن عضو به‌صورت inline:

```json
{"vals_list": [{
  "name": "…",
  "internal_permission": "none",
  "article_member_ids": [[0, 0, {"partner_id": 3, "permission": "write"}]]
}]}
```
→ **200** `[35]`، و `knowledge.article.member` ردیف `{partner_id: 3, permission: 'write'}` را ثبت کرد؛
بعد از `unlink` مقاله، اعضا هم پاک شدند (articles 32 / members 1 — همان مقدار قبل از تست).

**پیامد برای معماری:** `storage/odoo.py` باید یا (الف) مقاله را با پرمیشن پیش‌فرض `write` بسازد و بعد
به `none` تغییر دهد، یا (ب) از همان ابتدا عضو write را inline بفرستد. تنها `create` سادهٔ `none`
قطعاً شکست می‌خورد.

## ۵. موجودی فیلدهای تأییدشده (از `fields_get` زنده)

`knowledge.article`:

| فیلد | نوع | store | readonly | توضیح |
|---|---|---|---|---|
| `name` | char | True | False | عنوان (در خطا با آیکون نمایش داده می‌شود؛ پیش‌فرض 📄) |
| `body` | html | True | False | اگر خالی بماند، به‌طور پیش‌فرض `<h1>{name}</h1>` می‌شود |
| `icon` | char | True | False | آیکون مقاله (char، نه selection) |
| `internal_permission` | selection | True | False | پیش‌فرض `write`؛ `none` بدون عضو → خطا (بخش ۴) |
| `article_member_ids` | one2many → `knowledge.article.member` | True | False | عضویت‌ها |
| `parent_id` | many2one | True | False | ساختار درختی (نگاشت پلتفرم به والد) |
| `is_article_visible_by_everyone` | boolean | True | False | |
| `article_properties` | properties (JSON) | True | False | محل واقعی نگاشت `source_url`/`tags`/`source_date` |
| `article_properties_definition` | properties_definition | True | False | اسکیمای همان properties |
| `cover_image_id` | many2one | True | False | |
| `full_width` | boolean | True | False | |
| `article_url` | char | **False** | **True** | خواندنی (read) هست ولی **قابل فیلتر نیست** → Dedup از آن ممکن نیست |

`knowledge.article.member`:

| فیلد | نوع | الزامی | مقادیر |
|---|---|---|---|
| `article_id` | many2one → `knowledge.article` | بله | — |
| `partner_id` | many2one → `res.partner` | بله | — |
| `permission` | selection | بله | `write` (Can edit) / `read` (Can read) / `none` (No access) |

تأیید مستقیم ادعای PRD: `article_url` واقعاً `store=False, readonly=True` است — یعنی برای Dedup
قابل استفاده نیست و Dedup باید روی `content.url UNIQUE` در SQLite + کش `odoo_article_id` بماند.

## ۶. تصمیم‌های قطعی‌شده برای پیاده‌سازی

1. `create` **فقط** با `vals_list` (خطای 422 در غیر این صورت) و `write` با `{ids, vals}`.
2. اگر مقاله invite-only لازم است، عضو write **در همان `create`** فرستاده شود (`article_member_ids`).
3. برای Dedup: `search_read` روی `article_properties` (stored) یا کش محلی `odoo_article_id` — نه `article_url`.
4. خروجی `create` لیست id است؛ اگر خالی/`false` بود، آینه باید غیرمخرب رد شود (نه exception به کاربر).
5. کلید فعلی مربوط به `admin` است و در T3 با کلید کاربر سرویس جایگزین می‌شود؛ عمق دسترسی کاربر سرویس باید
   حداقلی باشد و در `res_users_apikeys` با scope `rpc`.
