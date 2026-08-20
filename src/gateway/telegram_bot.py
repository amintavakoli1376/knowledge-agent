"""Telegram Bot gateway."""
import asyncio
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from ..config import settings
from ..utils.url_parser import detect_platform, is_valid_url
from ..extractors.website import WebsiteExtractor
from ..extractors.arxiv import ArxivExtractor
from ..processors.summarizer import ContentSummarizer
from ..storage.notion import NotionStorage
from ..storage import sqlite_store
from ..models import ExtractedContent
from ..rag.chunker import chunk_text
from ..rag.embedder import get_embedder
from ..rag import vector_store
from ..rag.rag_engine import RagEngine

logger = logging.getLogger(__name__)
URL_REGEX = re.compile(r'(https?://[^\s]+|(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)')


def extract_first_url(text: str) -> str | None:
    """اولین لینک رو از متن استخراج می‌کنه (حتی اگه بدون http:// باشه)."""
    if not text:
        return None
    m = URL_REGEX.search(text)
    if not m:
        return None
    url = m.group(1)
    if not url.startswith('http'):
        url = 'https://' + url
    return url.strip()


class TelegramBot:
    """Telegram bot for receiving and processing content links."""
    
    def __init__(self):
        self.token = settings.telegram_bot_token
        self.extractors = {}
        self.summarizer = ContentSummarizer()
        self.storage = NotionStorage()
        self.application = None
    
    def set_extractors(self, extractors: dict):
        """Set the extractor instances from main app."""
        self.extractors = extractors
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "👋 **به دستیار دانش خوش آمدید!**\n\n"
            "هر لینکی بفرستید تا خلاصه‌سازی و در Notion ذخیره شود.\n\n"
            "🧠 برای جستجو در دانش، بنویسید:\n"
            "`/ask سوال شما`\n"
            "یا مستقیم سوال بپرسید (مثلاً: «بهترین مقاله درباره RAG چیست؟»)\n\n"
            "🚀 **پلتفرم‌های پشتیبانی‌شده:**\n"
            "📄 ArXiv\n"
            "🌐 وب‌سایت‌ها\n"
            "🎥 YouTube\n"
            "🐦 X / Twitter\n"
            "📸 اینستاگرام\n"
            "💼 لینکدین\n"
            "💬 کانال تلگرام\n"
            "📎 فایل PDF\n\n"
            "فقط کافیست لینک را بفرستید! 🚀"
        )
    
    async def ask_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /ask <question> command."""
        if not update.message:
            return
        question = " ".join(context.args or [])
        if not question:
            await update.message.reply_text(
                "🧠 فرمت: `/ask سوال شما`\nمثلاً: `/ask بهترین مقاله درباره RAG چیست؟`"
            )
            return
        await self._ask_rag(update, question)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages: links/raw posts (saved) OR questions (RAG /ask)."""
        if not update.message:
            return
        
        # استخراج متن از پیام یا کپشن فوروارد
        text = (update.message.text or update.message.caption or "").strip()
        if not text:
            return
        
        # اگر پیام با /ask شروع شد یا /ask سوال آمد → RAG
        if text.lower().startswith("/ask") or text.lower().startswith("سوال"):
            question = re.sub(r"^/ask\s*", "", text, flags=re.IGNORECASE).strip()
            question = re.sub(r"^سوال\s*", "", question, flags=re.IGNORECASE).strip()
            if question:
                await self._ask_rag(update, question)
                return
            await update.message.reply_text(
                "🧠 فرمت: `/ask سوال شما`\nمثلاً: `/ask بهترین مقاله درباره RAG چیست؟`"
            )
            return
        
        # پیدا کردن لینک t.me به عنوان «لینک پست» (اگه هست)
        tme_match = re.search(r'https?://t\.me/[^\s]+', text)
        post_url = tme_match.group(0) if tme_match else ""
        
        # لینک واقعی هست → پردازش/ذخیره
        url = extract_first_url(text)
        real_link = url and not url.startswith("https://t.me") and not url.startswith("http://t.me")
        
        if real_link and not post_url:
            await self._process_link(update, url)
            return
        
        # پست تلگرام (فوروارد یا متن با t.me) → ذخیره
        if post_url or real_link or tme_match:
            await self._process_raw_post(update, text, post_url)
            return
        
        # هیچ لینکی نیست → سوال در نظر بگیر و RAG جواب بده
        await self._ask_rag(update, text)
    
    async def _ask_rag(self, update: Update, question: str):
        """RAG: retrieve from knowledge base and answer."""
        processing = await update.message.reply_text("🧠 در حال جستجو در پایگاه دانش...")
        try:
            engine = RagEngine()
            answer = await engine.ask(question)
            await processing.edit_text(answer)
        except Exception as e:
            logger.error(f"RAG ask failed: {e}")
            await processing.edit_text(
                f"❌ **خطا در جستجوی دانش**\n\n`{str(e)[:200]}`"
            )
    
    async def _process_link(self, update: Update, url: str):
        """پردازش لینک (استخراج + خلاصه + ذخیره)."""
        processing_msg = await update.message.reply_text(
            "⏳ در حال پردازش لینک شما..."
        )
        try:
            platform = detect_platform(url)
            platform_emoji = {
                'arxiv': '📄', 'youtube': '🎥', 'twitter': '🐦',
                'linkedin': '💼', 'instagram': '📸', 'github': '💻',
                'medium': '✍️', 'telegram': '💬'
            }
            emoji = platform_emoji.get(platform, '🌐')
            
            await processing_msg.edit_text(
                f"{emoji} **تشخیص:** {platform.capitalize()}\n📥 در حال استخراج محتوا..."
            )
            
            extractor = self.extractors.get(platform) or self.extractors.get('website')
            
            await processing_msg.edit_text(f"📥 در حال استخراج محتوا از {platform}...")
            content = await extractor.extract(url)
            sqlite_store.save_content(content)
            await self._embed_content(content)
            
            await processing_msg.edit_text("🤖 در حال تحلیل با هوش مصنوعی...")
            analysis = await self.summarizer.analyze(content)
            sqlite_store.save_analysis(url, analysis)
            
            await processing_msg.edit_text("💾 در حال ذخیره در Notion...")
            notion_url = await self.storage.save(content, analysis)
            
            await self._send_result(update, content, analysis, notion_url)
            
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            await processing_msg.edit_text(
                f"❌ **خطا در پردازش لینک**\n\n"
                f"`{str(e)[:200]}`\n\n"
                "لطفاً دوباره تلاش کنید یا لینک دیگری بفرستید."
            )
    
    async def _embed_content(self, content: ExtractedContent) -> None:
        """Chunk + embed content into vector store (best-effort, non-blocking)."""
        try:
            text = content.full_text or content.title or ""
            chunks = chunk_text(text)
            if not chunks:
                return
            embedder = get_embedder()
            vectors = await asyncio.to_thread(embedder.embed, chunks)
            
            # پیدا کردن content_id از URL
            conn = sqlite_store._get_connection()
            try:
                row = conn.execute(
                    "SELECT id FROM content WHERE url = ?", (content.url,)
                ).fetchone()
            finally:
                conn.close()
            if row:
                vector_store.store_chunks(row["id"], chunks, vectors)
        except Exception as e:
            logger.warning(f"Embedding failed (non-blocking): {e}")
    
    async def _process_raw_post(self, update: Update, text: str, post_url: str = ""):
        """پردازش پست خام (مثل فوروارد تلگرام): متن پست رو خلاصه کن و لینک t.me رو به عنوان منبع نگه دار."""
        processing_msg = await update.message.reply_text(
            "⏳ در حال تحلیل پست شما..."
        )
        try:
            # متن پست رو مستقیم به عنوان محتوا می‌دیم (نه لینک داخلش)
            # لینک‌های داخل متن توی خلاصه می‌مونن چون summarizer کل متن رو می‌بینه
            content = ExtractedContent(
                url=post_url,  # لینک t.me به عنوان لینک پست
                title=text[:80],
                full_text=text,
                platform="telegram",
            )
            sqlite_store.save_content(content)
            await self._embed_content(content)
            
            await processing_msg.edit_text("🤖 در حال تحلیل با هوش مصنوعی...")
            analysis = await self.summarizer.analyze(content)
            sqlite_store.save_analysis(post_url or text, analysis)
            
            await processing_msg.edit_text("💾 در حال ذخیره در Notion...")
            notion_url = await self.storage.save(content, analysis)
            
            await self._send_result(update, content, analysis, notion_url)
            
        except Exception as e:
            logger.error(f"Error processing raw post: {e}")
            await processing_msg.edit_text(
                f"❌ **خطا در پردازش پست**\n\n"
                f"`{str(e)[:200]}`"
            )
    
    async def _send_result(self, update: Update, content: ExtractedContent, 
                          analysis, notion_url: str):
        """Send formatted result to user."""
        platform_emoji = {
            'arxiv': '📄', 'website': '🌐', 'youtube': '🎥',
            'twitter': '🐦', 'linkedin': '💼', 'instagram': '📸',
            'github': '💻', 'medium': '✍️'
        }
        emoji = platform_emoji.get(content.platform, '🔗')
        
        # Truncate summaries for Telegram
        summary_fa = analysis.summary_fa[:300] + "..." if len(analysis.summary_fa) > 300 else analysis.summary_fa
        summary_en = analysis.summary_en[:200] + "..." if len(analysis.summary_en) > 200 else analysis.summary_en
        
        tags_text = ' · '.join(f"#{t}" for t in analysis.tags[:5]) if analysis.tags else ""
        
        message = (
            f"✅ **در Notion ذخیره شد!**\n\n"
            f"{emoji} **{content.title[:80]}**\n"
            f"📂 `{analysis.category}` · ⭐ `{analysis.priority}`\n\n"
            f"**خلاصه فارسی:**\n{summary_fa}\n\n"
            f"**Summary:**\n{summary_en}\n\n"
        )
        
        if tags_text:
            message += f"🏷️ {tags_text}\n\n"
        
        if notion_url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 مشاهده در Knowledge Base (بدون لاگین)", url=notion_url)],
                [InlineKeyboardButton("🔗 لینک اصلی", url=content.url)]
            ])
            await update.message.reply_text(message, reply_markup=keyboard)
        else:
            await update.message.reply_text(message + "\n*(Notion API not configured)*")
    
    async def setup(self):
        """Set up the Telegram bot application with polling."""
        if not self.token:
            logger.warning("TELEGRAM_BOT_TOKEN not set. Bot disabled.")
            return
        
        self.application = Application.builder().token(self.token).build()
        
        # اطمینان از وجود جدول‌های برداری
        vector_store.init_vector_tables()
        
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.start))
        self.application.add_handler(CommandHandler("ask", self.ask_command))
        self.application.add_handler(
            MessageHandler(
                (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
                self.handle_message
            )
        )
        
        await self.application.initialize()
        await self.application.start()
        
        # Use polling instead of webhook (works on localhost)
        await self.application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "channel_post", "callback_query"],
        )
        
        logger.info("Telegram bot started (polling mode)")
    
    async def stop(self):
        """Stop the Telegram bot."""
        if self.application:
            if self.application.updater:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
    
    async def process_webhook(self, update_data: dict) -> None:
        """Process an incoming webhook update."""
        if self.application:
            update = Update.de_json(update_data, self.application.bot)
            await self.application.process_update(update)