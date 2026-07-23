"""Knowledge Agent — Main Application.

FastAPI server that handles Telegram webhooks and provides a REST API
for saving content to Notion.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .gateway.telegram_bot import TelegramBot
from .utils.url_parser import detect_platform, is_valid_url
from .extractors.website import WebsiteExtractor
from .extractors.arxiv import ArxivExtractor
from .processors.summarizer import ContentSummarizer
from .storage.notion import NotionStorage
from .models import SaveRequest, SaveResponse

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global instances
bot = TelegramBot()
extractors = {}
summarizer = ContentSummarizer()
storage = NotionStorage()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    logger.info("Starting Knowledge Agent...")
    
    # Initialize extractors
    global extractors
    extractors = {
        'arxiv': ArxivExtractor(),
        'website': WebsiteExtractor(),
    }
    
    # Set up Telegram bot (optional — don't fail if unavailable)
    if settings.telegram_bot_token:
        try:
            await bot.setup()
            logger.info("Telegram bot initialized")
        except Exception as e:
            logger.warning(f"Telegram bot init failed: {e}. Bot will be disabled.")
            logger.warning("To use Telegram bot, ensure network access to api.telegram.org")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set. Telegram bot disabled.")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Knowledge Agent...")
    for ext in extractors.values():
        if hasattr(ext, 'close'):
            try:
                await ext.close()
            except:
                pass


app = FastAPI(
    title="Knowledge Agent",
    description="Multi-source knowledge management agent. Save content from any platform to Notion.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "telegram_bot": bool(settings.telegram_bot_token),
        "notion": bool(settings.notion_api_key),
        "openai": bool(settings.openai_api_key),
    }


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram bot webhook endpoint."""
    if not settings.telegram_bot_token:
        return JSONResponse(
            status_code=503,
            content={"error": "Telegram bot not configured"}
        )
    
    try:
        update_data = await request.json()
        await bot.process_webhook(update_data)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/api/save", response_model=SaveResponse)
async def api_save(request: SaveRequest):
    """Direct API endpoint to save content."""
    if not settings.notion_api_key:
        return SaveResponse(
            success=False,
            error="Notion API not configured. Set NOTION_API_KEY in .env"
        )
    
    url = request.url.strip()
    if not is_valid_url(url):
        return SaveResponse(
            success=False,
            error="Invalid URL provided. Must start with http:// or https://"
        )
    
    try:
        # Detect platform
        platform = request.platform or detect_platform(url)
        
        # Select extractor
        if platform == 'arxiv':
            extractor = extractors['arxiv']
        else:
            extractor = extractors['website']
        
        # Step 1: Extract
        logger.info(f"Extracting content from {platform}: {url}")
        content = await extractor.extract(url)
        
        # Step 2: Summarize
        logger.info("Analyzing content with AI...")
        analysis = await summarizer.analyze(content)
        
        # Step 3: Save to Notion
        logger.info("Saving to Notion...")
        notion_url = await storage.save(content, analysis)
        
        return SaveResponse(
            success=True,
            notion_url=notion_url,
            title=content.title[:100],
            platform=platform,
            category=analysis.category,
        )
        
    except Exception as e:
        logger.error(f"Save failed: {e}")
        return SaveResponse(
            success=False,
            error=str(e)[:500]
        )


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Knowledge Agent",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "telegram_webhook": "POST /webhook/telegram",
            "api_save": "POST /api/save",
        },
        "status": {
            "telegram_bot": "✅ Configured" if settings.telegram_bot_token else "❌ Not configured",
            "notion": "✅ Configured" if settings.notion_api_key else "❌ Not configured",
            "openai": "✅ Configured" if settings.openai_api_key else "❌ Not configured",
        },
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
