# گزارش: مهاجرت embedding به ONNX int8 + راه‌اندازی سرویس روی سرور جدید

**تاریخ:** ۲۷ مهر ۱۴۰۵ (19 October 2026)
**سرور:** `141.11.175.39` · سرویس: `knowledge-agent.service` روی پورت `8080`
**وضعیت:** سرویس بالا و سالم؛ دادهٔ قبلی روی این سرور موجود نیست (پایین توضیح داده شده)

---

## ۱. چه چیزی عوض شد

### الف) Embedder از torch به ONNX Runtime
`src/rag/embedder.py` کامل بازنویسی شد:

- مدل: همان `intfloat/multilingual-e5-small`، ولی نسخهٔ رسمی **int8** (`onnx/model_qint8_avx512_vnni.onnx` = ۱۱۸.۳MB)
- اجرا با `onnxruntime` + `tokenizers` — **بدون torch و بدون sentence-transformers**
- mean pooling با attention mask + L2 normalize (معادل رفتار sentence-transformers)
- session مشترک و lazy؛ دیگر مدل در هر فراخوانی لود/آزاد نمی‌شود (رفتار قبلی که فقط ۱۴۷MB آزاد می‌کرد)

### ب) prefix های e5 که قبلاً زده نمی‌شد
- `embed_query()` → `"query: "` (در `rag_engine.ask`)
- `embed_passages()` → `"passage: "` (در هر دو اسکریپت reindex)
- `embed()` قدیمی حفظ شد (بدون prefix) برای سازگاری عقب‌رو

### ج) نیازمندی‌ها و مسیرهای شکسته
- `requirements.txt`: `pymupdf>=24.0` (غیرقابل‌حل) → `pymupdf>=1.26`
- اضافه شد: `onnxruntime`, `tokenizers`, `numpy`, `huggingface_hub`, `jinja2`, `markdown-it-py`, `mdit-py-plugins` (هر سه در کد ایمپورت می‌شدند ولی در requirements نبودند و نصب تازه را می‌شکستند)
- `scripts/download_embedding_model.py`: دانلود مدل؛ پوشهٔ `models/` در `.gitignore` است

### د) `/index` بدون Outline هم بالا می‌آید
`src/render/views.py`: اگر `OUTLINE_ENABLED=false` باشد، صفحهٔ شاخص از `knowledge.db` رندر می‌شود و ۵۰۳ نمی‌دهد. قبل از این تغییر، `/index` بعد از ۳۰ ثانیه تایم‌اوت ۵۰۳ برمی‌گرداند.

---

## ۲. اندازه‌گیری واقعی (همین سرور)

| سنجه | قبل (torch + fp32) | بعد (ONNX int8) |
|---|---|---|
| ایمپورت کتابخانه‌ها | ۴۲۹MB | **۵۳MB** |
| اوج مصرف با مدل لودشده | ۹۱۶MB | **۴۷۷MB** |
| مصرف پایدار بعد از encode | ۷۶۹MB | **۴۷۵MB** |
| دیسک مدل | ۴۷۰.۶MB | **۱۱۸.۳MB** |
| حجم venv | ۱.۷GB | **۴۳۱MB** |
| torch در فرایند | بله | **خیر** |

کیفیت و صحت:
- جست‌وجوی تستی فارسی: سند مرتبط `0.9193` در برابر نامرتبط‌ها `0.72–0.79` → رتبهٔ اول درست
- encode چهار passage: `0.10s` · batch ۳۲ متن: `0.37s` (~۸۶ متن/ثانیه)
- **drift کوانتایز** (int8 در برابر fp32 با همان ورودی): کسینوس `0.9966`، حداکثر اختلاف ماتریس شباهت `0.0066` → عملاً ناچیز

---

## ۳. وضعیت سرویس

```
systemctl status knowledge-agent   # active
curl localhost:8080/health
{"status":"ok","version":"1.0.0","telegram_bot":false,"notion":false,"openai":true}
```

- `/index`, `/api/stats`, `/api/docs`, `/api/graph`, `/api/concepts` همه `200` (index در ۲۸ms)
- RSS فرایند در حالت بیکار: **~۱۵۰MB** (مدل فقط هنگام اولین درخواست embedding لود می‌شود)
- watchdog (`knowledge-watchdog.sh`) به `systemctl restart` تغییر کرد تا با systemd تعارض نکند
- تست‌ها: `47 passed`

---

## ۴. چیزهایی که روی این سرور نیست

1. **دادهٔ قبلی**: `knowledge.db` با ~۸۴ سند روی سرور قبلی (`176.100.37.230`) بود که الان از اینجا **در دسترس نیست** (پورت ۸۰۸۰ و ۳۰۱۳ هیچ پاسخی نمی‌دهند). این نصب با دیتابیس خالی بالا آمده است.
2. **کلیدهای Telegram و Notion** روی این سرور وجود ندارند (`telegram_bot:false`, `notion:false`). فقط کلید LLM هست.
3. **Outline** بالاست؟ نه — `OUTLINE_ENABLED=false`، بنابراین رجیستری اسناد خالی است و `/view/{doc_id}` سندی ندارد.

برای بازگرداندن کامل: فایل `knowledge.db` سرور قبلی + `TELEGRAM_BOT_TOKEN` + `NOTION_API_KEY`/`NOTION_DATABASE_ID` لازم است.
