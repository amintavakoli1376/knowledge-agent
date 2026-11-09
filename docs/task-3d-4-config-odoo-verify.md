# Task 3D-4 — تنظیمات Odoo در config.py (ODOO_ENABLED) — تأیید

- Epic: «پیکربندی و Secrets» — تسک 4 (3D-4)
- Branch: `epic2/odoo-mirror`
- تاریخ: 2026-11-09

## وضعیت

این تسک **قبلاً انجام شده** بود (اپیک ۲) — اینجا فقط تأیید شد، کد جدیدی نوشته
نشد:

- `src/config.py:35-39` → `odoo_enabled/url/db/api_key/uid` + `odoo_body_size_limit`
- `src/config.py:87-95` → `@model_validator` که در `ODOO_ENABLED=true` نبودن فیلدهای
  لازم را با `ValidationError` رد می‌کند.
- `src/main.py:296` → استفاده از `odoo_enabled`.

## تأیید انجام

1. **رفتار validator** (live):
   - `Settings(odoo_enabled=True, odoo_url=…, odoo_db=…, odoo_api_key=…, odoo_uid=5)` → OK.
   - `Settings(odoo_enabled=True, … بدون odoo_api_key)` → `ValidationError` (درست).
   - `Settings(odoo_enabled=False)` → بدون فیلد، معتبر.
2. **تست‌ها**: `pytest tests/test_config.py tests/test_odoo_storage.py -q` → `7 passed`.
3. **عدم Regression**: `pytest tests/ -q` → `150 passed, 1 skipped` (خط پایه 147/1
   + 3 تست جدید rotation).
4. **`scripts/diff_report.py`** → `RESULT: CLEAN`.
