# Task 3 — تعریف کاربر سرویس و عضویت در Workspace

**Epic:** Discovery و Validation فنی (Spike) · **فاز:** phase-1 · **SP:** 3
**تاریخ:** 2026-11-04 · **وضعیت:** Done (با تست زندهٔ مشاهده‌پذیری)
**مرجع:** `NFR-002` — «مقاله ساخته می‌شود ولی برای کسی دیده نمی‌شود» اگر کاربر سرویس عضو Workspace نباشد.

هدف: ساخت هویت حداقلی برای آینهٔ Odoo، ساخت/انتخاب Workspace مقصد، و **اثبات تجربی** قاعدهٔ مشاهده‌پذیری
با یک کاربر عادی (نه با ادمین که همه‌چیز را می‌بیند).

---

## ۱. چه چیزی ساخته شد (شناسه‌های قطعی برای پیاده‌سازی)

| مورد | مقدار |
|---|---|
| کاربر سرویس | `knowledge-agent@local` · `res.users` id=**5** · نام «Knowledge Agent (Service)» · partner id=6 |
| گروه کاربر سرویس | فقط `base.group_user` (در Odoo 19: «Role / Member») — حداقلی |
| Workspace مقصد | `knowledge.article` id=**37** · نام **Knowledge Agent** · `parent_id` خالی (ریشه) · `internal_permission='none'` (Members only) · `is_article_visible_by_everyone=false` |
| عضویت سرویس در Workspace | `knowledge.article.member`: `partner_id=6`, `permission='write'` |
| اسکیمای Properties روی Workspace | `source_url` (char)، `tags` (char)، `source_date` (date)، `platform` (char) |
| کلید API سرویس | `res_users_apikeys` id=**2** · name «Knowledge Agent mirror (T3)» · scope `rpc` · انقضا **2027-02-02** (۹۰ روز) |
| محل کلید | `/root/odoo/.ka_service_key` (`chmod 600`) — در پیاده‌سازی به `ODOO_API_KEY` در `.env` منتقل می‌شود |
| کلید T2 (ادمین) | **revoke شد** — با کلید آن `401 Invalid apikey` تأیید شد (بدهی T2 بسته شد) |
| لینک Workspace | `http://141.11.175.39:8069/knowledge/article/37` |

## ۲. دسترسی‌ها (از `ir.model.access.csv` واقعی ماژول Knowledge)

| مدل | `base.group_user` (Internal User) | `base.group_system` |
|---|---|---|
| `knowledge.article` | read=1, write=1, **create=1**, **unlink=0** | read/write/create/unlink |
| `knowledge.article.member` | **read=1**، write=0، create=0، unlink=0 | کامل |

**دو نتیجهٔ معماری:**
1. کاربر سرویس **می‌تواند مقاله بسازد/ویرایش کند ولی نمی‌تواند پاک کند** — این «حداقل دسترسی» عمدی است و
   یعنی **حذف/جبران خطا وظیفهٔ ادمین است** (آینه باید اشتباه را گزارش کند، نه پاک کند).
2. **عضویت را فقط ادمین می‌تواند تعیین کند** (`knowledge.article.member` برای کاربر عادی فقط خواندنی است).
   پس تأیید/حذف عضویت‌ها یک عملیات setup است، نه چیزی که آینه در هر اجرا انجام دهد.

## ۳. اثبات تجربی `NFR-002` (تست مشاهده‌پذیری با کاربر عادی)

یک کاربر عادی موقت (`ka-visibility-test@local`، فقط `base.group_user`) با کلید API جداگانه ساخته شد:

| مرحله | کاربر عادی | ادمین |
|---|---|---|
| قبل از عضویت — مشاهدهٔ Workspace 37 | **HTTP 200 `[]`** (هیچ ردیفی) | `[{id:37, …}]` |
| قبل از عضویت — مشاهدهٔ مقالهٔ فرزند (probe) | **HTTP 200 `[]`** | `[{id:39, name:'T3 probe …', parent_id:[37,…]}]` |
| پس از افزودن عضویت `read` (توسط ادمین) | **`[{id:37…}]` و `[{id:39…}]`** | — |

یعنی: مقاله در دیتابیس **وجود دارد**، ادمین آن را می‌بیند، ولی کاربر داخلی که عضو نیست **صفر ردیف** می‌گیرد.
این دقیقاً همان حالتی است که در `NFR-002` به‌عنوان شکست خاموش هشدار داده شده بود — حالا با عدد تأیید شده است.

**حداقل دسترسی هم اثبات شد:** کاربر سرویس `create` داخل Workspace → **200**، ولی `unlink` همان مقاله → **403**
(`You are not allowed to delete 'Knowledge Article' … allowed for: Role / Administrator`).

## ۴. یافتهٔ مهم: اسکیمای Properties روی **والد** تعریف می‌شود، نه روی فرزند

در مدل واقعی: `article_properties = fields.Properties('Properties', definition="parent_id.article_properties_definition")`.
پس اگر والد اسکیما نداشته باشد، نوشتن مقدار روی فرزند **بی‌صدا دور ریخته می‌شود** (در تست ما `article_properties` بعد از
`create` مقدار `[]` برگرداند — بدون هیچ خطایی). پس از تعریف اسکیما روی Workspace 37، مقدارها ماندند:

```json
[{"name":"source_url","type":"char","value":"https://example.com/updated"},
 {"name":"tags","type":"char","value":"هوش-مصنوعی, آزمایشی"},
 {"name":"source_date","type":"date","value":"2026-11-04"},
 {"name":"platform","type":"char","value":"website"}]
```

**و مهم‌تر:** جست‌وجو روی property **کار می‌کند**:
`domain=[["article_properties.source_url","=","…"]]` → مقاله پیدا شد.
این یک مسیر **دوم** برای Dedup/بازیابی فراهم می‌کند (مسیر اول همچنان `content.url UNIQUE` در SQLite است).

## ۵. تله‌هایی که در پیاده‌سازی باید رعایت شوند

1. **هر کاربر جدید یک مقالهٔ «Welcome \<name\>» خودکار می‌گیرد** (icon 👋، `internal_permission='none'`، با عضویت
   write برای خودش) — کاربر سرویس هم مقالهٔ id=36 را گرفت. آینه باید **فقط مقاله‌های خودش** (زیر Workspace 37) را
   بشمارد، نه همهٔ ۳۴ مقاله‌ی موجود را.
2. **حذف آخرین عضو write یک مقالهٔ invite-only خطای ValidationError می‌دهد** مگر context
   `knowledge_member_skip_writable_check=True` ست شود. (با همین راهکار پاک‌سازی انجام شد.)
3. **مقالهٔ ریشه باید `internal_permission` داشته باشد** (قید `_check_permission_on_root`)؛ و
   **`is_article_item=True` فقط با داشتن والد مجاز است** (قید `check(is_article_item IS NOT TRUE OR parent_id IS NOT NULL)`)
   → ریشه = Workspace، فرزند = Item.
4. **`internal_permission` گزینه‌ها:** `write` = Can edit · `read` = Can read · `none` = **Members only**
   (برای فرزندان مقدار خالی/ارث‌بری طبیعی است؛ در تست `False` برگشت).
5. `sudo()` در Odoo 19 کاربر جاری را **عوض نمی‌کند** (فقط دسترسی‌ها را دور می‌زند) — به همین دلیل
   `with_user(user).sudo()._generate(...)` کلید را برای همان کاربر سرویس می‌سازد (نه برای superuser).

## ۶. پاک‌سازی و حالت نهایی (بدون زباله)

| شاخص | قبل از تست‌ها | بعد از پاک‌سازی |
|---|---|---|
| مقاله‌ها | ۳۲ | **۳۴** = ۳۲ اصلی + Workspace «Knowledge Agent» (37) + مقالهٔ خودکار Welcome سرویس (36) |
| اعضای مقاله | ۱ | **۳** (۳۲ ادمین، ۳۶ سرویس، ۳۷ سرویس) |
| کاربران فعال | ۲ (admin, service) | ۲ — کاربر تست حذف شد |
| کلیدهای API | ۰ | **۱** = فقط کلید کاربر سرویس (کلید T2 ادمین revoke شد) |

مقاله‌های probe (ids 39، 40، 41) همه حذف شدند و شمارش به حالت مورد انتظار برگشت (بدون Traceback در اسکریپت‌ها).

## ۷. تصمیم‌های قطعی برای پیاده‌سازی آینه

1. `ODOO_WORKSPACE_ROOT_ID=37` (Workspace «Knowledge Agent») و `ODOO_API_KEY` = محتوای `/root/odoo/.ka_service_key`.
2. ساخت مقاله: `create` با `{"vals_list": [{name, parent_id: 37, is_article_item: True, icon, body, article_properties}]}`
   — و اسکیمای Properties **فقط یک‌بار** روی 37 تعریف شود، نه روی هر آیتم.
3. عضویت‌ها و حذف‌ها کار **ادمین** است: آینه فقط create/write می‌کند و خطای 403 را «نیازمند اقدام ادمین» گزارش می‌کند.
4. برای دیده‌شدن محتوا توسط کاربران سازمانی، یا باید **عضویت read** بگیرند یا پرچم
   `is_article_visible_by_everyone` روی Workspace روشن شود. انتخاب یکی از این دو، سؤال باز
   `internal_permission پیش‌فرض` در T4 است (توصیهٔ فنی: پرچم «visible by everyone» برای دانش سازمانی، چون
   مدیریت اعضای تک‌تک کاربران مقیاس‌پذیر نیست).
5. اگر روزی اعضای زیادی لازم شد: عضویت‌ها با `knowledge.article.member.create` (با پارامتر `article_id`) انجام شود،
   نه با inline member در `create` (inline فقط برای گام اول کار می‌کند و قابل تکرار نیست).
