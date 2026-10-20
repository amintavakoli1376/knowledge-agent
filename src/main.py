"""Knowledge Agent — Main Application.

FastAPI server that handles Telegram webhooks and provides a REST API
for saving content to Notion.
"""
import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .gateway.telegram_bot import TelegramBot
from .utils.errors import friendly_error
from .utils.url_parser import detect_platform, is_valid_url
from .extractors.website import WebsiteExtractor
from .extractors.arxiv import ArxivExtractor
from .extractors.youtube import YouTubeExtractor
from .extractors.linkedin import LinkedInExtractor
from .extractors.twitter import TwitterExtractor
from .extractors.instagram import InstagramExtractor
from .extractors.pdf import PDFExtractor
from .extractors.telegram_channel import TelegramChannelExtractor
from .processors.summarizer import ContentSummarizer, QuotaExceededError
from .storage.notion import NotionStorage
from .storage import sqlite_store
from .models import SaveRequest, SaveResponse

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# ⚠️ لاگ DEBUG برای کتابخانهٔ تلگرام/httpx توکن ربات را در URL هر درخواست چاپ می‌کند.
# فقط وقتی لازم است لاگ کامل دید، با TELEGRAM_DEBUG=1 روشن شود.
_tg_level = logging.DEBUG if os.getenv("TELEGRAM_DEBUG") == "1" else logging.WARNING
logging.getLogger("telegram").setLevel(_tg_level)
logging.getLogger("httpx").setLevel(logging.WARNING if _tg_level != logging.DEBUG else logging.INFO)
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
        'youtube': YouTubeExtractor(),
        'twitter': TwitterExtractor(),
        'linkedin': LinkedInExtractor(),
        'instagram': InstagramExtractor(),
        'pdf': PDFExtractor(),
        'telegram': TelegramChannelExtractor(),
        'website': WebsiteExtractor(),
    }
    
    # Set up Telegram bot (optional — don't fail if unavailable)
    if settings.telegram_bot_token:
        try:
            bot.set_extractors(extractors)
            await bot.setup()
            logger.info("Telegram bot initialized")
        except Exception as e:
            logger.warning(f"Telegram bot init failed: {e}. Bot will be disabled.")
            logger.warning("To use Telegram bot, ensure network access to api.telegram.org")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set. Telegram bot disabled.")

    # ریکاوری خودکار خلاصه‌های ناقص (سهمیهٔ AI 429) — پس از setup بات،
    # به‌صورت پس‌زمینه‌ای؛ هرگز startup را نمی‌بندد.
    from .processors.recovery import recover_pending_safe

    asyncio.create_task(recover_pending_safe(storage=storage))

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
    # Swagger/OpenAPI فقط وقتی سرور محلی است؛ روی IP عمومی نقشهٔ API لو می‌رود
    docs_url="/docs" if os.getenv("KA_EXPOSE_DOCS") == "1" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if os.getenv("KA_EXPOSE_DOCS") == "1" else None,
)

# Static assets for the Kaghaz (کاغذ) RTL reader
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")

# کاغذ view routes (/index, /view/{doc_id})
from .render.views import router as kaghaz_router  # noqa: E402

app.include_router(kaghaz_router)

# کاغذ JSON API (فاز U1): /api/docs, /api/docs/{id}, /api/tree, /api/stats
from .render.api_views import router as kaghaz_api_router  # noqa: E402

app.include_router(kaghaz_api_router)


# ---------------------------------------------------------------- auth (وب)
from .render import auth as web_auth  # noqa: E402


@app.middleware("http")
async def require_web_auth(request: Request, call_next):
    """قفل همهٔ مسیرها به‌جز /health و /static و صفحهٔ ورود."""
    if web_auth.is_public(request.url.path):
        return await call_next(request)
    if not web_auth.authorized(request, settings.web_auth_token):
        return web_auth.unauthorized(request, bool(settings.web_auth_token))
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
async def login_form(next: str = "/index"):
    return web_auth.login_page(next)


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    token = str(form.get("token", "")).strip()
    nxt = str(form.get("next", "/index") or "/index")
    if not web_auth._same(token, settings.web_auth_token):
        await asyncio.sleep(0.6)  # کند کردن حدس توکن
        return web_auth.login_page(nxt, "توکن درست نیست.")
    resp = RedirectResponse(nxt if nxt.startswith("/") else "/index", status_code=303)
    resp.set_cookie(
        web_auth.COOKIE_NAME,
        web_auth._cookie_value(settings.web_auth_token),
        max_age=web_auth.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(web_auth.COOKIE_NAME, path="/")
    return resp

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
            content={"error": "internal error"},
        )


@app.post("/api/save", response_model=SaveResponse)
async def api_save(request: SaveRequest):
    """Direct API endpoint to save content.

    Notion اختیاری است: اگر NOTION_API_KEY ست نشده باشد، محتوا در SQLite (و در صورت
    فعال بودن، Outline) ذخیره می‌شود و پاسخ موفق برمی‌گردد — قبلاً همین‌جا با خطا
    برمی‌گشت و مسیر ذخیره‌سازی را کامل می‌بست.
    """
    url = request.url.strip()
    if not is_valid_url(url):
        return SaveResponse(
            success=False,
            error="Invalid URL provided. Must start with http:// or https://"
        )
    
    try:
        # Detect platform
        platform = request.platform or detect_platform(url)
        
        # Select extractor based on platform
        if platform == 'arxiv':
            extractor = extractors['arxiv']
        elif platform == 'youtube':
            extractor = extractors['youtube']
        elif platform == 'twitter':
            extractor = extractors['twitter']
        elif platform == 'linkedin':
            extractor = extractors['linkedin']
        elif platform == 'instagram':
            extractor = extractors['instagram']
        elif platform == 'telegram':
            extractor = extractors['telegram']
        elif platform == 'pdf' or url.lower().endswith('.pdf'):
            extractor = extractors['pdf']
        else:
            extractor = extractors['website']
        
        # Step 1: Extract
        logger.info(f"Extracting content from {platform}: {url}")
        content = await extractor.extract(url)
        
        # Step 1.5: Store full text in SQLite (best-effort, for future RAG)
        sqlite_store.save_content(content)
        logger.info("Full content saved to SQLite")
        
        # Step 2: Summarize
        logger.info("Analyzing content with AI...")
        try:
            analysis = await summarizer.analyze(content)
        except QuotaExceededError as e:
            # سهمیهٔ AI تمام شده — رکورد با flag ذخیره می‌شود؛ بعداً خودکار خلاصه می‌شود
            from .processors.summarizer import _quota_pending_analysis

            analysis = _quota_pending_analysis(e)
            logger.warning(f"AI quota exhausted for {url} — saved as pending_resummarize")

        # Step 2.5: Update SQLite row with AI analysis fields
        # نکته: URL اکسترکتور (content.url) ممکن است نرمال‌شده باشد؛ همان را بده
        sqlite_store.save_analysis(content.url, analysis)
        logger.info("Analysis saved to SQLite")
        # embedding غیرهمزمان — مثل مسیر تلگرام
        try:
            asyncio.create_task(bot._embed_content(content))
        except Exception as e:  # noqa: BLE001
            logger.debug("embedding task skipped: %s", e)
        
        # Step 3: Save to Notion
        logger.info("Saving to Notion...")
        notion_url = await storage.save(content, analysis)

        # Step 3.5: Also save to Outline (best-effort — never breaks the flow)
        outline_url = None
        if settings.outline_enabled:
            from .storage.outline import OutlineStorage
            try:
                logger.info("Saving to Outline (parallel)...")
                outline_url = await asyncio.to_thread(OutlineStorage().save, content, analysis)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Outline save failed (non-fatal): {e}")

        return SaveResponse(
            success=True,
            notion_url=notion_url,
            outline_url=outline_url,
            title=content.title[:100],
            platform=platform,
            category=analysis.category,
        )
        
    except Exception as e:
        logger.error(f"Save failed: {type(e).__name__}: {e}")
        return SaveResponse(success=False, error=friendly_error(e, "save"))


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
