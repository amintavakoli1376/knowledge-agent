# Odoo 19 Enterprise — Deployment & Knowledge Integration

این پوشه، استقرار (deployment) **Odoo 19 Enterprise** روی VPS را نگه می‌دارد و بخشی از
ریپوی یکپارچه (monorepo) **Knowledge Agent** است. کد اصلی Knowledge Agent در `src/`
و `scripts/` بالای همین ریپو قرار دارد.

> **منبع حقیقت:** این پوشه مرجع مستندات و کانفیگ Odoo است. سرور زنده
> (`/root/odoo` روی VPS) همان‌جا که هست اجرا می‌شود؛ این ریپو نسخه‌ی
> قابل‌بازبینی و نسخه‌بندی‌شده‌ی همان است.

---

## ساختار

```
odoo/
├── compose.yaml                     # Docker Compose: odoo (8069) + PostgreSQL (pgvector 16)
├── config/
│   └── odoo.conf.template           # الگوی کانفیگ — کپی کنید و رازها را واقعی کنید
├── docs/
│   ├── odoo-enterprise-setup.md     # راه‌اندازی اولیه Odoo Enterprise
│   ├── odoo-usage-guide.md          # راهنمای استفاده از سرویس
│   └── prd-ai-chatbot.md            # PRD چت‌بات AI متصل به Knowledge
└── .gitignore                       # گارد امنیتی (رازها، بکاپ‌ها، enterprise_addons)
```

## نکته‌ی امنیتی مهم

👆 **فایل واقعی `config/odoo.conf` هرگز در گیت نیست** — شامل `admin_passwd` و
`db_password` است. در عوض الگوی `odoo.conf.template` نگهداری می‌شود. برای استقرار:

```bash
cp odoo/config/odoo.conf.template /root/odoo/config/odoo.conf
# سپس admin_passwd و db_password را با مقادیر واقعی جایگزین کنید
```

---

## استقرار (Deployment)

پیش‌نیاز: `docker` و `docker compose` روی سرور.

```bash
cd /root/odoo

# ۱. تنظیم رازها
cp .env.example .env   # DB_PASSWORD را واقعی کنید
cp config/odoo.conf.template config/odoo.conf
# admin_passwd را در odoo.conf تغییر دهید

# ۲. بالا آوردن کانتینرها
docker compose up -d

# ۳. بررسی سلامت
docker compose ps
curl -fsS http://localhost:8069/web/health
```

| سرویس | پورت | نکته |
|--------|------|------|
| Odoo | `8069` (همه) | UI مدیریتی |
| PostgreSQL | `127.0.0.1:5544` | فقط localhost — خارج VPS قابل‌دسترس نیست |

> `enterprise_addons/` (کد اختصاصی Odoo Enterprise) و `core_dupes/` در گیت نیستند؛
> به‌صورت دستی/جداگانه روی سرور قرار می‌گیرند و در `compose.yaml` به‌صورت read-only
> به کانتینر mount می‌شوند.

---

## یکپارچه‌سازی با Knowledge Agent

دیتابیس‌های Odoo:
- **production** — دیتابیس اصلی (۶۷ مقاله).
- **ka_test** — محیط آزمون (۳۴ مقاله).

Knowledge Agent (`src/storage/odoo.py`) از طریق REST API Odoo مقالات را به workspaceها
میرور می‌کند (parent per platform تحت ریشه). اسکریپت‌های مرتبط:
- `scripts/migrate_to_odoo.py` — backfill از PG به Odoo (FR-010).
- `scripts/rotate_odoo_key.py` — چرخش کلید سرویس.

دیتابیس گراف (`data/knowledge.db`) شامل مفاهیم و اسناد است که در Odoo Knowledge
به‌صورت workspace «مفاهیم (Graph)» (۸۲) نمایش داده می‌شود.

---

## بکاپ

بکاپ‌های دیتابیس در `backups/` (محلی، خارج از گیت) گرفته می‌شوند و طبق سیاست
پروژه فقط به **Arvan S3** ارسال می‌شوند؛ تلگرام فقط برای هشدار استفاده می‌شود.
پیش از هر تغییر زنده در دیتابیس، بکاپ بگیرید.

---

## منابع مرتبط

- `docs/prd-ai-chatbot.md` — PRD چت‌بات AI متصل به Knowledge (ماژول‌های
  `ai_livechat` / `ai_app` / `ai_knowledge`).
- `docs/odoo-usage-guide.md` — راهنمای استفاده از سرویس Odoo.
- `docs/odoo-enterprise-setup.md` — راه‌اندازی اولیه Odoo Enterprise.
