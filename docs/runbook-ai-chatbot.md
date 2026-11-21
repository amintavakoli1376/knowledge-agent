# Runbook — Odoo AI Chatbot (تحویل)

نسخه: ۱.۰ — تاریخ: 2026-11-21
استقرار: **production** (و محیط تست `ka_test`). مدل: `inceptionlabs/mercury-3` (روتر Avalai).

---

## ۱. نمای کلی معماری

- دو دیتابیس روی Postgres (`odoo-db`): `production` (زنده) و `ka_test` (تست).
- کانتینر `odoo-app` ماژولهای AI را سرویس میدهد؛ کد اضافهشده روی هاست:
  `/root/odoo/enterprise_addons/` (mount فقطخواندنی در `odoo-app:/mnt/extra-addons`).
- مسیر AI: کاربر → Livechat (کانال) → `ai.agent` (Mercury) → RAG (`ai_embedding` روی منابع Knowledge) → پاسخ فارسی + پاورقی «منابع:».
- روتر LLM: `http://141.11.175.39:20128/v1`؛ کلید در `ir.config_parameter.ai.openai_key`.

موجودیتهای فعلی:
- Agent production: **id=4** «مشاور دانش Odoo» — `restrict_to_sources=True`، منبع: ریشهٔ 37 (کل workspace Knowledge Agent).
- کانال livechat: **id=2** «مشاور دانش Odoo» + rule **id=1** (`ai_agent_id=4`, `action=display_button`).
- Agent ka_test: id=5؛ کانال id=2؛ rule id=1.

## ۲. پیشنیازها

- دسترسی به دو کانتینر: `docker exec odoo-app ...` و `docker exec odoo-db psql -U odoo -d <db> ...`.
- کلید روتر در `/root/knowledge-agent-repo/.env` (`OPENAI_API_KEY`).
- مدل embedding: `Avalai/text-embedding-3-small` (فقط همین توسط روتر پذیرفته میشود).

## ۳. نصب و پیکربندی (روش تأییدشده)

### ۳.۱ نصب ماژولها
```bash
docker exec odoo-app odoo -d ka_test -i ai_app --stop-after-init --no-http
docker exec odoo-app odoo -d ka_test -i ai_livechat,im_livechat --stop-after-init --no-http
# برای production همان دستورها با -d production
```
ترتیب مهم است: `ai_app` اول (کشش `ai_fields`/`ai_server_actions`/`ai_knowledge`)، بعد `ai_livechat,im_livechat`.

### ۳.۲ پیکربندی provider (پارامترها)
```
ai.openai_key   = <OPENAI_API_KEY>
openai_key_enabled = true
ai.base_url      = http://141.11.175.39:20128/v1
```
تنظیم از طریق odoo shell (با `env.cr.commit()` صریح).

### ۳.۳ پچهای کد (روی هاست)
| فایل | تغییر |
|---|---|
| `ai/utils/llm_api_service.py` | خواندن `ai.base_url` + `stream=False` |
| `ai/utils/llm_providers.py` | افزودن `inceptionlabs/mercury-3` و `Avalai/...` به انتخابها؛ `get_provider_for_embedding_model` |
| `ai/models/ai_agent.py` | `_get_embedding_model` (پیشوند `Avalai/`)، `_build_rag_context` (لینک)، `_append_citations` (پاورقی قطعی) |
| `ai_livechat/models/im_livechat_channel.py` | `_compute_ai_agent_count` (پچ recordset) |

بعد از هر پچ: `docker restart odoo-app`.

### ۳.۴ ساخت Agent (مهم: `name` الزامی است)
`ai_agent.create` خودش `res_partner` میسازد؛ حتماً `name` بدهید، وگرنه `res_partner_check_name` شکست میخورد.
فیلدها: `name`, `partner_id` (خودکار), `llm_model=inceptionlabs/mercury-3`, `response_style=balanced`, `system_prompt` (فارسی), `restrict_to_sources=True`.
منابع: `create_from_articles([<root_id>], agent.id)`.

### ۳.۵ دسترسی به مقالات
برای اینکه منبع قابل استفاده باشد، برای هر مقاله member با `permission='read'` برای partner داخلی (مثلاً `OdooBot` = 2) بسازید:
`knowledge.article.member` ← `{article_id, partner_id, permission:'read'}`.

### ۳.۶ پردازش و embedding
```python
env['ai.agent.source']._cron_process_sources()   # ساخت attachment
env.cr.commit()
env['ai.embedding']._cron_generate_embedding()    # chunk + بردار
env.cr.commit()
```
بعد از آن بررسی: `status='indexed'`، `MISSING_VECTORS=0`.

### ۳.۷ کانال livechat
- کانال `im_livechat.channel` {name, button_text, default_message, رنگهای M3, max_sessions_mode:'unlimited'}.
- rule `im_livechat.channel.rule` {channel_id, ai_agent_id, action:'display_button', chatbot_enabled_condition:'always', sequence:10, regex_url:''}.
- بررسی: `channel.ai_agent_count == 1` و `channel._is_livechat_available() == True`.

## ۴. بکاپ و ریکاوری (الزامی قبل از هر تغییر)

```bash
BK=/root/backups/odoo_ai_chatbot/$(date +%F)
docker exec odoo-db pg_dump -U odoo -Fc production > $BK/production.dump
docker exec odoo-db pg_dump -U odoo -Fc ka_test  > $BK/ka_test.dump
docker exec odoo-app cat /etc/odoo/odoo.conf > $BK/odoo.conf
```
تست ریکاوری (در temp DB):
```bash
docker exec odoo-db psql -U odoo -d postgres -c "CREATE DATABASE restore_test WITH TEMPLATE template0;"
docker cp $BK/production.dump odoo-db:/tmp/prod.dump
docker exec odoo-db pg_restore -U odoo -d restore_test --no-owner --role=odoo /tmp/prod.dump
docker exec odoo-db psql -U odoo -d restore_test -tc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
# باید با live برابر باشد (الح: 240)
docker exec odoo-db psql -U odoo -d postgres -c "DROP DATABASE restore_test;"
```
نکته: رستور باید از داخل کانتینر (با `docker cp`) انجام شود؛ `pg_restore` از stdin میزبان کار نمیکند.

## ۵. عیبیابی (مشکلات شناختهشده)

| نشانه | علت | راهکار |
|---|---|---|
| `status='failed'` + پیام «Embedding generation failed…» | chunkها با مدل bare ساخته شدهاند | حذف chunkهای `text-embedding-3-small`، set status=processing، دوباره `_cron_generate_embedding()` |
| پاورقی «منابع:» خالی یا دوتایی | مدل خودش هدر را میسازد (گاه با `**منابع:**` یا بولت) و کد قبلی چیزی اضافه نمیکرد | پچ `_append_citations` (پچ موجود): بلاک انتهایی را با آخرین رخداد `منابع:` (با دونقطه) حذف میکند تا وسط متن (مثل «منابع موجود») قطع نشود؛ پاسخهای چندپیامی قبل از append یکی میشوند؛ هر منبع در خط خودش، بدون بولت مارکداون |
| `ai_agent_count=0` | `_read_group` کلید recordset برمیگرداند، کد با `channel.id` میخواند | پچ `_compute_ai_agent_count` (پچ موجود) |
| خطای اولیه در ساخت Agent | پاس ندادن `name` | به `create` مقدار `name` بدهید |
| `pg_restore: input file is too short` | رستور از stdin میزبان | از `docker cp` + مسیر داخل کانتینر استفاده کنید |
| تداخل کُرون (`SerializationFailure`) | فراخوانی مستقیم generate همزمان با کُرون سرور | یا صبر کنید سرور تمام کند، یا همزمان صدا نزنید |
| کانف کلید/مدل تنظیم نشد | `psql -v` پارامتر نامدار را جایگزین نمیکند | از odoo shell + `set_param` استفاده کنید |
| هشدار collation mismatch | نسخه collation | بیخطر، ignore کنید |

### نکات عمومی
- اسکریپتهای odoo shell **حتماً** `env.cr.commit()` صریح داشته باشند (وگرنه rollback میشود).
- `psql` همیشه `-d` صریح بگیرد (دیتابیس پیشفرض `odoo` وجود ندارد).
- `CREATE DATABASE` فقط با `TEMPLATE template0` (template1 خطای collation میدهد).
- هر پچ روی کد: اول بکاپ بگیرید، بعد از اعمال کانتینر را ریاستارت کنید.

## ۶. نگهداری

- پایش: `docker logs odoo-app` برای خطاهای `llm_api_service` (API calls).
- افزودن مقاله/منبع جدید: مثل ۳.۵–۳.۶.
- بهروزرسانی مدل/روتر: `llm_providers.py` + `ir.config_parameter` + بازساخت embedding.
- بکاپ دورهای طبق بخش ۴؛ ارسال فقط به Arvan S3 (تلگرام = فقط هشدار).
