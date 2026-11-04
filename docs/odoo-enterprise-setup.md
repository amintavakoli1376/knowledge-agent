# Odoo سازمانی — راه‌اندازی و رفع اختلاف نسخه

تاریخ: ۲۰۲۶-۱۱-۰۴

## خلاصه
نصب Odoo Enterprise روی VPS شکست خورده بود چون ماژول‌های سازمانی با هسته‌ی
Odoo هم‌نسخه نبودند. با پین‌کردن ایمیج به همان بیلدی که بسته‌ی enterprise از آن
آمده، مشکل ریشه‌ای حل شد.

## علت ریشه‌ای
| مورد | قبل | بعد |
|---|---|---|
| ایمیج | `odoo:19` → بیلد `19.0-20261102` | `odoo:19.0-20250918` |
| بسته‌ی enterprise | `19.0+e.20250918` | همان |
| نسخه‌ی ماژول `account` | هسته ۱.۵ / enterprise ۱.۴ | هر دو ۱.۴ |
| نتیجه | ماژول `knowledge` هنگام نصب خطا می‌داد | نصب شد |

۱۴ ماه فاصله بین هسته و ماژول‌های سازمانی باعث ناسازگاری مدل‌ها و ویوها می‌شد.

## کارهای انجام‌شده
1. **حذف ماژول‌های تکراری:** از ۱۳۶۲ پوشه در `enterprise_addons`، ۶۴۵ مورد کپی
   هسته بود (نسخه‌ی قدیمی که روی نسخه‌ی جدید سایه می‌انداخت). به `core_dupes/`
   منتقل شد و ۷۱۷ ماژول خالص enterprise باقی ماند.
2. **پین ایمیج:** `compose.yaml` → `image: odoo:19.0-20250918`
   (بکاپ: `compose.yaml.bak.*`)
3. **بازسازی دیتابیس:** دیتابیس `production` خالی بود (۲۴MB، ۱۹ ماژول پایه، بدون
   جدول فروش/حسابداری) → drop و ایجاد مجدد.
4. **نصب `web_enterprise`** با CLI:
   `odoo -d production -i web_enterprise --stop-after-init --no-http --without-demo=all`
5. **تست دود (smoke test):** نصب `knowledge` — همان ماژولی که قبلاً خطا می‌داد —
   موفق بود (۴۷ ماژول نصب‌شده).
6. **پاک‌سازی نویز لاگ:** هشدار `invalid addons directory '/var/lib/odoo/addons/19.0'`
   بی‌خطر است (مسیر در کانفیگ هست، پوشه ساخته نشده).

## وضعیت فعلی
- `odoo-app`: ایمیج `odoo:19.0-20250918`، healthy، پورت `8069`
- `odoo-db`: `postgres:16`، healthy
- `http://<server>:8069/web/login` → 200
- ۱۳۵۵ ماژول در رجیستری، ۱۳۳۳ آماده‌ی نصب (شامل `account_accountant`، `helpdesk`،
  `mrp_workorder`، `quality_control`، `sign`، `documents`، `web_studio`)

## نکات نگهداری
- **هرگز ایمیج را بدون هم‌نسخه‌کردن بسته‌ی enterprise آپدیت نکن.** نسخه‌ی ایمیج
  باید با نسخه‌ی `.deb` یکی باشد (`19.0+e.YYYYMMDD` ↔ `19.0-YYYYMMDD`).
- برای دیدن اپ‌های جدید بعد از افزودن ماژول: Apps → Update Apps List.
- نصب ماژول از CLI وقتی سرور خاموش است انجام شود تا رجیستری دوباره ساخته شود.
- `config/odoo.conf` و `.env` شامل رمز هستند و در git نیستند.

## دستورهای پرکاربرد
```bash
cd /root/odoo
docker compose up -d                                  # بالا آوردن
docker compose run --rm odoo odoo -d production -i <module> --stop-after-init --no-http
docker exec odoo-db psql -U odoo -d production -tAc \
  "select name,state from ir_module_module where state='installed' order by name;"
```
