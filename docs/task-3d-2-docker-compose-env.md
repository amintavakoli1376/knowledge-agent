# Task 3D-2 — docker-compose env

- Epic: «پیکربندی و Secrets» — تسک 2 (3D-2)
- Branch: `epic2/odoo-mirror`
- تاریخ: 2026-11-09

## تغییرات `docker-compose.yml`

1. **حذف `version:`** — در Compose v2 منسوخ است و خطای هشدار می‌دهد.
2. **افزودن `container_name: knowledge-agent`** — نام کانتینر صریح.
3. **افزودن `healthcheck`** — به‌جای curl (که در ایمیج `python:3.11-slim` نیست)
   از `python -c urllib` روی `http://127.0.0.1:8080/health` استفاده شده
   (interval 30s / retries 3 / start_period 30s).
4. **مستندسازی متغیرهای `ODOO_*`** — به‌صورت comment در بالای فایل؛ خود
   متغیرها هاردکد نشده‌اند و از طریق `env_file: .env` پاس داده می‌شوند.

## تأیید انجام

- `docker compose config --quiet` → بدون خطا.
- اثبات عبور `ODOO_*` از `env_file`: با یک `.env` آزمایشی شامل
  `ODOO_ENABLED/URL/DB/API_KEY/UID/BODY_SIZE_LIMIT`، `docker compose config`
  همه را در خروجی نشان داد (`ODOO_API_KEY` در گزارش فقط به‌صورت redacted).
