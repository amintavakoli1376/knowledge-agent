# Task 3D-3 — .env.example و README

- Epic: «پیکربندی و Secrets» — تسک 3 (3D-3)
- Branch: `epic2/odoo-mirror`
- تاریخ: 2026-11-09

## `.env.example`

بلوک‌های ازدست‌رفته اضافه شد:

- `ODOO_*` — `ODOO_ENABLED` / `ODOO_URL` / `ODOO_DB` / `ODOO_API_KEY` /
  `ODOO_UID` / `ODOO_BODY_SIZE_LIMIT`
- `BRIGHTDATA_KEY`
- `PG_*` — `PG_ENABLED` / `PG_HOST` / `PG_PORT` / `PG_USER` / `PG_PASSWORD` /
  `PG_DB` / `PG_SCHEMA`
- `WEB_AUTH_TOKEN`
- `TELEGRAM_CHAT_ID`

## README

بخش جدید «Odoo 19 Mirror» اضافه شد: چیستی (آینه اختیاری، flag-gated و
non-fatal)، متغیرهای لازم، نحوه فعال‌سازی (`ODOO_ENABLED=true` + ری‌استارت)، و
منابع (کلاینت، اسکریپت rotation، PRD، تست‌ها).

## تأیید انجام

- `cp .env.example .env` اکنون همه کلیدهای لازم برای راه‌اندازی کامل (از جمله
  ODOO_*، PG_*، BRIGHTDATA_KEY، WEB_AUTH_TOKEN) را پوشش می‌دهد.
- README گام‌به‌گام است و مقطع «Odoo mirror» را کامل پوشش می‌دهد.
