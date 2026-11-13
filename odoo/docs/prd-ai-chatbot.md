# PRD — چتبات هوش مصنوعی متصل به دانش Odoo (AI Livechat)

> **وضعیت:** پیشنویس — برای تأیید
> **تاریخ:** 2026-11-13
> **محصول:** Odoo 19 Enterprise (Docker روی VPS)
> **نویسنده:** Hermes (برای Amin)

---

## ۱. خلاصهی اجرایی

یک چتبات هوش مصنوعی داخل Odoo بسازیم که به **دانش/مفاهیم موجود در Knowledge** پاسخ میدهد.
کاربر (Amin) سؤال میپرسد، چتبات با تکیه بر محتوای workspaceهای Knowledge (مثل «Knowledge Agent» و «مفاهیم (Graph)») پاسخ میدهد.

**آنچه میسازیم:** فعالسازی ماژولهای AI اکوسیستم Odoo (`ai_app`, `ai_livechat`, `ai_knowledge`) با یک provider از LLM.

---

## ۲. زمینه و وضعیت فعلی (حقیقت موجود)

| مورد | وضعیت |
|---|---|
| نسخهی Odoo | 19 Enterprise، ایمج `odoo:19.0-20250918` |
| دیتابیسها | `production` (۶۸ مقاله)، `ka_test` (۳۴ مقاله) |
| ماژولهای AI | همگی در `enterprise_addons/` موجودند، **هیچکدام نصب نیستند** |
| `ai_auto_install` | نصب است (میدان تست) |
| `mail_bot` | نصب است (ربات سادهی داخلی — ربطی به چتبات AI ندارد) |
| `im_livechat` | uninstalled (وابستگی `ai_livechat`) |
| ساختار Knowledge | workspace ۳۷ «Knowledge Agent» + زیرشاخهها؛ workspace ۸۲ «مفاهیم (Graph)» + ۱۳ مفهوم |

### وابستگیهای زنجیرهای ماژولها

```
ai_livechat → ai_app + im_livechat
ai_app       → ai + attachment_indexation
ai_knowledge → ai + knowledge
```

### محدودیت مهم: providerهای LLM

در کد Odoo (`ai/utils/llm_api_service.py` و `llm_providers.py`) **فقط دو provider** پشتیبانی میشود و `base_url` آنها سختکد است:

| Provider | base_url | کلید |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `ai.openai_key` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `ai.gemini_key` |

> ⚠️ **گزینهی «URL سفارشی» برای provider سوم وجود ندارد.** استفاده از Avalai خودمان نیازمند **پچ کد** روی `llm_api_service.py` است (تغییر `base_url` به `http://141.11.175.39:20128/v1`).

---

## ۳. اهداف و غیراهداف

### اهداف (Goals)
- G1: چتبات AI داخل Odoo که به پرسشهای کاربر، **بر اساس دانش موجود در Knowledge** پاسخ دهد.
- G2: پاسخها به **مفاهیم** ارجاع دهند (مثل «مدل زبانی»، «RAG») و لینک به مقالههای مرتبط.
- G3: در `ka_test` توسعه و تست شود؛ پس از تأیید، در `production` مستقر شود.

### غیراهداف (Non-Goals)
- N1: چتبات عمومی/فاقد زمینه (مثل ChatGPT باز) — نه؛ فقط متصل به دانش.
- N2: استفاده از `mail_bot` (ربات سادهی داخلی) — ربطی به این پروژه ندارد.
- N3: اپراتور انسانی (livechat انسانی) — خارج از این PRD.
- N4: ساخت گراف تعاملی جدید — این PRD فقط پاسخگویی متنی است.

---

## ۴. کاربران و سناریوها

| Persona | نیاز |
|---|---|
| **Amin** (مدیر/صاحب محصول) | پرسیدن سؤال از دانش، دیدن پاسخ با ارجاع به مفاهیم |
| **همکاران/کاربران داخلی** | جستوجوی دانش پروژه به زبان طبیعی |

### سناریوهای کلیدی (User Stories)
- US1: «بهعنوان کاربر، میخواهم بپرسم «مدل زبانی چیست؟» و پاسخی ارجاعدادهشده به مفاهیمِ Knowledge بگیرم.»
- US2: «بهعنوان کاربر، میخواهم پاسخها شامل لینک به مقالههای منبعِ Odoo باشند.»
- US3: «بهعنوان مدیر، میخواهم چتبات فقط به workspaceهای مشخصشده (نه کل دیتابیس) دسترسی داشته باشد.»

---

## ۵. الزامات کارکردی (Functional Requirements)

| ID | الزام | اولویت |
|---|---|---|
| FR1 | نصب ماژولهای AI (`ai_app`, `ai_livechat`, `ai_knowledge`) در `ka_test` | Must |
| FR2 | تنظیم provider LLM (Gemini یا Avalai) در تنظیمات AI | Must |
| FR3 | تعریف «عامل AI» (AI Agent) که به Knowledge متصل باشد | Must |
| FR4 | پاسخدهی با ارجاع به مفاهیم/مقالات Knowledge | Must |
| FR5 | محدودکردن دسترسی چتبات به workspaceهای منتخب | Should |
| FR6 | پیادهسازی ویجت چت در UI (داخل Odoo) | Should |

---

## ۶. الزامات غیرکارکردی (Non-Functional)

| ID | الزام | معیار |
|---|---|---|
| NFR1 | امنیت: دسترسی به Knowledge فقط برای کاربران مجاز | بدون نشت داده |
| NFR2 | تأخیر پاسخ | < ۵ ثانیه (در حالت عادی) |
| NFR3 | پایداری: هیچ آسیب به دادههای موجود production | تغییرات فقط در `ka_test` تا تأیید |
| NFR4 | نگهداری: بکاپ قبل از هر تغییر زنده | روال ثابت |
| NFR5 | زبان/واژه: واژههای فنی لاتین به فارسی درست بیان شوند | رعایت در متن UI/پاسخها |

---

## ۷. معماری فنی

```
[کاربر] → UI Odoo (AI Livechat widget)
               │
               ▼
        ai_livechat (channel)
               │
               ▼
        ai_app (AI Agent)
               │
               ├──► ai_knowledge ← Knowledge (workspace 37/82 و...)
               │
               ▼
        LLM API (Gemini یا Avalai)
```

### نقاط تصمیم (Decision Points)
- **DP1 — انتخاب provider:** Gemini (بدون کد) یا Avalai (نیاز به پچ کد).
- **DP2 — دامنهی دانش:** دقیقاً کدام workspaceها در دسترس چتبات باشند (پیشنهاد: ۳۷ و ۸۲).
- **DP3 — جای استقرار تست:** `ka_test` اول، بعد `production`.

---

## ۸. طرح اجرا (مرحلهای)

### فاز ۰ — بررسی feasibility (بدون تغییر زنده)
- ثبت `ai_app` در `ka_test` و بررسی لاگهای نیازمندی.
- بررسی در دسترس بودن کلید Gemini یا امکان وصل Avalai.

### فاز ۱ — نصب در `ka_test`
- نصب `ai_app` → `ai_livechat` → `ai_knowledge` در `ka_test`.
- تنظیم provider در `Settings → AI`.

### فاز ۲ — اتصال به Knowledge
- تعریف AI Agent و مشخصکردن workspaceهای منبع (۳۷/۸۲).
- تست با پرسشهای واقعی (مثل «متنباز چیست؟»).

### فاز ۳ — تأیید و استقرار در `production`
- بکاپ production قبل از هر چیزی.
- نصب و پیکربندی در production.
- تست نهایی با کاربر واقعی.

---

## ۹. معیارهای پذیرش (Acceptance Criteria)

- [ ] AC1: ماژولهای AI در `ka_test` نصب و فعالاند.
- [ ] AC2: چتبات به سؤال «مدل زبانی چیست؟» با ارجاع به مفاهیم Knowledge پاسخ میدهد.
- [ ] AC3: پاسخ شامل (link) به مقالههای منبع است.
- [ ] AC4: دسترسی چتبات فقط به workspaceهای منتخب محدود است.
- [ ] AC5: هیچ تغییری در دادههای موجود `production` (قبل از فاز ۳) رخ نمیدهد.

---

## ۱۰. ریسکها و گشودگیها

| ریسک | اثر | کاهش |
|---|---|---|
| نیاز به کلید Gemini/OpenAI خارجی | وابستگی خارجی | بررسی Avalai با پچ کد |
| پچ کد روی ماژول enterprise | مخدوششدن بهروزرسانیها | مستندسازی پچ؛ نگهداری جداگانه |
| دانشِ پاسخها به کیفیت محتوای Knowledge وابسته است | پاسخهای ناقص | بهبود محتوای مفاهیم |
| فعالسازی نادرست دسترسی | نشت داده | محدودکردن workspaceها |

### سؤالات باز (Open Questions)
- OQ1: کدام provider؟ (Gemini ساده / Avalai با پچ)
- OQ2: دقیقاً کدام workspaceها در دسترس باشند؟
- OQ3: آیا چتبات باید **عمومی** (برای مراجعهکنندگان وبسایت) باشد یا صرفاً **داخلی**؟

---

## ۱۱. ضمیمه: فایلهای کلیدی

| مسیر | نقش |
|---|---|
| `enterprise_addons/ai_app/` | ماژول AI (هسته) |
| `enterprise_addons/ai_livechat/` | چتبات AI |
| `enterprise_addons/ai_knowledge/` | اتصال AI به Knowledge |
| `enterprise_addons/ai/utils/llm_api_service.py` | محل پچ احتمالی برای Avalai |
| `enterprise_addons/ai/utils/llm_providers.py` | فهرست providerها |
| `config/odoo.conf` | کانفیگ Odoo |
| `backups/` | بکاپهای قبل از تغییر |
