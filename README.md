# Knowledge Agent
## Multi-Source Knowledge Management Agent — Save Anything to Notion

**Version:** 1.0.0  
**Author:** Hermes Agent  
**License:** MIT

---

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Edit .env with your API keys

# 2. Start Zen API Proxy (free AI models: DeepSeek, Qwen)
git clone https://github.com/bigdata2211it-web/opencode-free-proxy.git
cd opencode-free-proxy && npm install && node server.mjs &
# → Proxy running on http://localhost:6446

# 3. Start Knowledge Agent
cd ../knowledge-agent
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8080

# 4. Install Browser Extension (optional)
# Open chrome://extensions → Developer mode → Load unpacked → select extension/
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram Bot token from @BotFather |
| `NOTION_API_KEY` | ✅ | Notion integration token |
| `NOTION_DATABASE_ID` | ✅ | Target database ID in Notion |
| `OPENAI_API_KEY` | ✅ | OpenAI API key for summarization |
| `HOST` | ❌ | Server host (default: 0.0.0.0) |
| `PORT` | ❌ | Server port (default: 8080) |

## Odoo 19 Mirror (KA → Odoo Knowledge)

The agent can publish saved documents to a self-hosted **Odoo 19 Enterprise**
Knowledge base as an optional third mirror (besides Notion / Outline). SQLite
remains the source of truth; the mirror is **flag-gated and non-fatal** — if
it is disabled or errors, saving still succeeds.

### Required variables (see `.env.example`)

| Variable | Description |
|----------|-------------|
| `ODOO_ENABLED` | Set `true` to enable the mirror (default `false`) |
| `ODOO_URL` | Base URL of the Odoo instance (e.g. `http://127.0.0.1:8069`) |
| `ODOO_DB` | Odoo database name (e.g. `production`) |
| `ODOO_API_KEY` | Service-user API key (rotate via `scripts/rotate_odoo_key.py`) |
| `ODOO_UID` | Odoo service-user id (KA service user = `5`) |
| `ODOO_BODY_SIZE_LIMIT` | Optional. Body-size threshold (chars) above which the full body is stored as an `ir.attachment` (default `50000`) |

### Enable

```bash
# .env
ODOO_ENABLED=true
ODOO_URL=http://127.0.0.1:8069
ODOO_DB=production
ODOO_API_KEY=<service-user key>
ODOO_UID=5
```

Then restart the service:

```bash
systemctl restart knowledge-agent      # bare systemd deployment
# or: docker compose up -d --build     # containerized deployment
```

### Resources

- Mirror client: `src/storage/odoo_client.py`, `src/storage/odoo.py`
- Key rotation: `scripts/rotate_odoo_key.py` (two-step, no key on stdout/commits)
- PRD + discovery docs: `docs/prd-knowledge-agent-odoo-v1.1.md`, `docs/task-*.md`
- Tests: `tests/test_odoo_*.py` (unit), `tests/test_odoo_integration.py` (live, skipped without test env)

## Project Structure

```
knowledge-agent/
├── src/
│   ├── main.py              # FastAPI app + entrypoint
│   ├── config.py            # Configuration + env vars
│   ├── gateway/
│   │   ├── __init__.py
│   │   └── telegram_bot.py  # Telegram bot handler
│   ├── extractors/
│   │   ├── __init__.py
│   │   ├── base.py          # Base extractor
│   │   ├── website.py       # General website scraper
│   │   └── arxiv.py         # ArXiv paper extractor
│   ├── processors/
│   │   ├── __init__.py
│   │   └── summarizer.py    # AI summarization
│   ├── storage/
│   │   ├── __init__.py
│   │   └── notion.py        # Notion API client
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py       # Data models
│   └── utils/
│       ├── __init__.py
│       └── url_parser.py    # URL detection
├── tests/
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

## Backup (Arvan S3)

بکاپ روزانهٔ vault در باکت اختصاصی `knowledge-backups` روی آروان ذخیره می‌شود
(`ARVAN_ENDPOINT` / `ARVAN_ACCESS_KEY` / `ARVAN_SECRET_KEY` / `ARVAN_REGION=ir-thr-at1` / `ARVAN_BUCKET=knowledge-backups`).

```bash
systemctl start knowledge-backup.service     # اجرای دستی
systemctl list-timers knowledge-backup.timer # زمان‌بندی: هر روز ۰۳:۱۵ (با catch-up)
journalctl -u knowledge-backup.service -n 20 # نتیجه
```

اسکریپت بعد از آپلود، فایل را دوباره دانلود و بازیابی می‌کند (تعداد اسناد را گزارش می‌دهد) و بعد retention را روی همان باکت اعمال می‌کند.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/webhook/telegram` | Telegram bot webhook |
| GET | `/health` | Health check |
| POST | `/api/save` | Direct API save endpoint |

## Architecture

```
Input (Telegram / API)
    │
    ▼
Platform Router ──► Content Extractor ──► AI Summarizer ──► Notion Storage
    │                      │                     │
    └── Instagram          └── Web Scraping      └── GPT-4o-mini
    └── LinkedIn            └── ArXiv API
    └── YouTube             └── YouTube API
    └── ArXiv               └── Instagram API
    └── Website             └── etc.
```
