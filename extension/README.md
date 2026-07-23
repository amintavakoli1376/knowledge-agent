# Knowledge Agent — Browser Extension 🧠

Save any page, article, or video to your Notion knowledge base with one click.

---

## 📋 پیش‌نیازها (قبل از نصب اکستنشن)

باید **Knowledge Agent Server** در حال اجرا باشه. دو راه داری:

### راه A — اجرای کامل (توصیه شده)

```bash
# ۱. کلون کن
git clone https://github.com/amintavakoli1376/knowledge-agent.git
cd knowledge-agent

# ۲. وابستگی‌ها رو نصب کن
pip install -r requirements.txt

# ۳. فایل .env رو با کلیدها پر کن
cp .env.example .env
# ویرایش کن: TELEGRAM_BOT_TOKEN, NOTION_API_KEY, NOTION_DATABASE_ID

# ۴. Zen API Proxy رو اجرا کن (برای AI رایگان)
cd /root/opencode-free-proxy  # یا هر جایی که clone کردی
node server.mjs &
# سرور روی localhost:6446 بالا میاد

# ۵. Agent رو اجرا کن
cd /root/knowledge-agent
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

### راه B — فقط API (اگر Agent جای دیگه‌ای داره اجرا می‌شه)

فقط کافیه یه سرور در حال اجرا داشته باشی با `POST /api/save` و `GET /health`.

---

## 🔧 نصب اکستنشن

### روش ۱: Developer Mode (توصیه شده)

| مرحله | توضیح |
|-------|--------|
| **۱** | مرورگر Chrome/Edge/Brave رو باز کن |
| **۲** | برو به `chrome://extensions` |
| **۳** | کلید **Developer mode** (بالا سمت راست) رو فعال کن |
| **۴** | دکمه **Load unpacked** رو بزن |
| **۵** | پوشه `extension/` رو از پروژه انتخاب کن |

### روش ۲: نصب از فایل ZIP

```bash
cd knowledge-agent
zip -r extension.zip extension/
# برو به chrome://extensions → drag & drop فایل ZIP
```

---

## 🚀 راه‌اندازی اولیه

بعد از نصب اکستنشن:

### ۱. ست کردن Server URL

۱. روی آیکون اکستنشن کلیک کن
۲. دکمه ⚙️ **Settings** رو بزن
۳. آدرس سرور رو وارد کن: `http://localhost:8080`
۴. **Save** رو بزن

### ۲. ایجاد دیتابیس Notion

> **قبل از این مرحله:** مطمئن شو API Key نوتion تو `.env` ست شده

۱. تو Notion یه **صفحه خالی** جدید باز کن
۲. از URL صفحه، **UUID** رو کپی کن
```
https://notion.so/MyPage-3a69d5f238ac...
                   └── این UUID ──┘
```
۳. تو اکستنشن دکمه 🆕 **Setup DB** رو بزن
۴. UUID رو Paste کن
۵. دکمه **✨ Create Database** رو بزن
۶. ✅ دیتابیس ساخته شد!

> **نکته مهم:** قبل از ستاپ، حتماً Notion Integration رو به صفحه‌ات متصل کن:
> • در Notion → دکمه **•••** → **Add connections** → اسم `hermes` رو انتخاب کن

### ۳. تست

۱. یه صفحه وب دلخواه باز کن
۲. دکمه اکستنشن رو بزن
۳. URL به صورت خودکار پر شده
۴. **Save to Notion** رو بزن
۵. ✅ اگه موفق باشه، نوتیفیکیشن می‌بینی

---

## 🎯 نحوه استفاده

| روش | توضیح | اسکرین‌شات |
|-----|-------|-----------|
| **📥 Toolbar icon** | کلیک روی آیکون → Save | — |
| **🖱️ Right-click page** | راست‌کلیک روی صفحه → "Save to Knowledge Agent" | — |
| **🔗 Right-click link** | راست‌کلیک روی لینک → "Save This Link to Notion" | — |
| **⌨️ Alt+K** | ذخیره فوری صفحه فعلی با کیبورد | — |
| **🌐 Custom URL** | پیست کردن هر لینک در پنجره popup | — |
| **🆕 Setup DB** | ساخت خودکار دیتابیس Notion | — |

### Keyboard Shortcuts

| کلید | مرورگر | توضیح |
|------|--------|-------|
| `Alt + K` | Windows / Linux | Save current page |
| `⌘ + K` | Mac | Save current page |

> تغییر Shortcuts: برو به `chrome://extensions/shortcuts`

---

## ⚙️ Settings

| تنظیم | توضیح | پیش‌فرض |
|-------|-------|---------|
| **Server URL** | آدرس سرور Agent | `http://localhost:8080` |
| **API Key** | (اختیاری) | — |

---

## 🔍 عیب‌یابی (Troubleshooting)

### ❌ "Cannot reach server"

```
علت: Agent در حال اجرا نیست
راه‌حل: uvicorn src.main:app --host 0.0.0.0 --port 8080
```

### ❌ "Notion API error"

```
علت: API Key یا Database ID اشتباهه
راه‌حل: 
  ۱. چک کن NOTION_API_KEY تو .env درسته
  ۲. از دکمه Setup DB استفاده کن
  ۳. مطمئن شو Integration به صفحه متصل شده
```

### ❌ "AI service error"

```
علت: Zen API Proxy (DeepSeek) اجرا نیست
راه‌حل: node server.mjs (در پوشه opencode-free-proxy)
```

### ❌ Bot تلگرام جواب نمی‌ده

```
علت: Webhook mode بود (برای localhost کار نمی‌کرد)
راه‌حل: از polling mode استفاده کن (ورژن فعلی)
```

---

## 🏗️ ساختار فایل‌ها

```
knowledge-agent/
├── extension/
│   ├── manifest.json     # تنظیمات اکستنشن
│   ├── background.js     # سرویس‌ورکر (context menu, save logic)
│   ├── popup.html        # پنجره popup
│   ├── popup.js          # منطق popup
│   ├── options.html      # صفحه تنظیمات
│   ├── icon16.png        # آیکون ۱۶px
│   ├── icon32.png        # آیکون ۳۲px
│   ├── icon48.png        # آیکون ۴۸px
│   ├── icon128.png       # آیکون ۱۲۸px
│   └── README.md         # این فایل
├── src/                  # کد سرور Agent
└── ...
```

---

## 📝 انتشار در Chrome Web Store

برای انتشار تجاری:

| مرحله | توضیح |
|-------|--------|
| **۱** | برو به https://chrome.google.com/webstore/devconsole |
| **۲** | ثبت‌نام کن (هزینه $۵ یکبار) |
| **۳** | New Item → آپلود extension.zip |
| **۴** | فرم رو پر کن (نام، توضیحات، اسکرین‌شات) |
| **۵** | Submit for Review → ۱-۳ روز |

---

## 💡 نکات

- اکستنشن با **Manifest V3** ساخته شده
- نیازی به کتابخانه خارجی نداره
- با Chrome, Edge, Brave, Opera سازگاره
- Settingsها از طریق `chrome.storage.sync` ذخیره می‌شن (بین دستگاه‌ها sync میشه)

---

**ساخته شده با ❤️ برای Knowledge Agent**
