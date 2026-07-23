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
