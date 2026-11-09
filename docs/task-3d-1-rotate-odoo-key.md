# Task 3D-1 — Rotation کلید API Odoo

- Epic: «پیکربندی و Secrets» — تسک 1 (3D-1)
- Branch: `epic2/odoo-mirror`
- تاریخ: 2026-11-09

## چیستی

اسکریپت `scripts/rotate_odoo_key.py` کلید API سرویس Knowledge Agent را در Odoo
به‌صورت ایمن عوض می‌کند — بدون اینکه کلید قدیمی یا جدید در stdout/لاگ/commit لو برود.

## طراحی (دو-گامی امن)

هدف از دو-گامی بودن این است که هیچ‌وقت سرویس بدون کلید معتبر نماند:

1. **تولید و ذخیره**: یک کلید جدید داخل کانتینر `odoo-app` با
   `odoo shell` + `res.users.apikeys._generate('rpc', ...)` ساخته می‌شود، در
   `--key-file` (پیش‌فرض `/root/odoo/.ka_service_key`) با `chmod 600` ذخیره
   می‌شود، و با یک `search_read` واقعی احراز هویت می‌گردد.
   - اگر تأیید شکست بخورد، فایل `.new` حذف و ردیف کلید تازه‌ساخته‌شده پاک
     می‌شود؛ کلید قدیمی دست‌نخورده می‌ماند.
2. **Swap و Revoke**: تنها پس از موفقیت گام ۱، کلید جدید به‌جای کلید قبلی جابه‌جا
   می‌شود و ردیف(های) کلید قدیمی در دیتابیس `unlink` می‌شود.

خروجی فقط «پیشوند sha256» کلید جدید را گزارش می‌دهد — هرگز خود کلید.

## استفاده

```bash
# چرخش کلید سرویس تولید (uid 5)
python3 scripts/rotate_odoo_key.py \
    --key-file /root/odoo/.ka_service_key \
    --db production --uid 5 --name "Knowledge Agent mirror (rotated)" \
    --expiration-days 90 --restart-service
```

گزینه‌ها: `--key-file` (مسیر فایل کلید) · `--db` (نام دیتابیس Odoo) ·
`--uid` (شناسه کاربر سرویس) · `--container` (نام کانتینر Odoo) ·
`--url` (پایه آدرس) · `--name` · `--expiration-days` · `--restart-service`
(ری‌استارت knowledge-agent + بررسی `/health` پس از swap).

## تأیید انجام (live)

- **Mechanism روی دیتابیس تست** (`ka_test`, uid 6): چرخش کامل انجام شد — ردیف
  قدیمی 1 revoke، ردیف جدید 2 ساخته، با `search_read` تأیید و swap شد؛ `search_count`
  روی کلید جدید = 3.
- **تولید** (`production`, uid 5): چرخش کامل انجام شد — ردیف قدیمی 2 revoke،
  ردیف جدید 5 ساخته (انقضا 2027-02-07)، فایل `/root/odoo/.ka_service_key`
  (chmod 600) با کلید جدید جایگزین شد؛ `search_read` با کلید جدید 2 ردیف برگرداند.
- معیار پذیرش «هیچ کلیدی در stdout/لاگ/commit» برقرار است: اسکریپت فقط پیشوند
  sha256 چاپ می‌کند و در این سند/commit هیچ کلیدی درج نشده است.
