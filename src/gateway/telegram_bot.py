"""Telegram Bot gateway."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from ..config import settings
from ..utils.url_parser import detect_platform, is_valid_url
from ..extractors.website import WebsiteExtractor
from ..extractors.arxiv import ArxivExtractor
from ..processors.summarizer import ContentSummarizer
from ..storage.notion import NotionStorage
from ..models import ExtractedContent

logger = logging.getLogger(__name__)


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
            "👋 **Welcome to Knowledge Agent!**\n\n"
            "Send me any link and I'll save it to your Notion database.\n\n"
            "**Supported platforms:**\n"
            "🌐 Any website · 📄 ArXiv papers\n"
            "📱 Instagram · 💼 LinkedIn · 🐦 Twitter/X\n"
            "🎥 YouTube · 💬 Telegram\n\n"
            "Just send me a URL and I'll take care of the rest! 🚀"
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages with URLs."""
        if not update.message or not update.message.text:
            return
        
        text = update.message.text.strip()
        
        # Check if it's a URL
        if not is_valid_url(text):
            await update.message.reply_text(
                "❌ Please send a valid URL.\n"
                "Example: `https://arxiv.org/abs/2401.12345`"
            )
            return
        
        # Send initial processing message
        processing_msg = await update.message.reply_text(
            "⏳ Processing your link..."
        )
        
        try:
            # Detect platform
            platform = detect_platform(text)
            platform_emoji = {
                'arxiv': '📄', 'youtube': '🎥', 'twitter': '🐦',
                'linkedin': '💼', 'instagram': '📸', 'github': '💻',
                'medium': '✍️', 'telegram': '💬'
            }
            emoji = platform_emoji.get(platform, '🌐')
            
            await processing_msg.edit_text(
                f"{emoji} **Detected:** {platform.capitalize()}\n📥 Extracting content..."
            )
            
            # Select extractor
            if platform in self.extractors:
                extractor = self.extractors[platform]
            elif platform == 'website':
                extractor = self.extractors.get('website')
            else:
                extractor = self.extractors.get('website')
            
            # Step 1: Extract
            await processing_msg.edit_text(f"📥 Extracting content from {platform}...")
            content = await extractor.extract(text)
            
            # Step 2: Summarize
            await processing_msg.edit_text("🤖 Analyzing with AI...")
            analysis = await self.summarizer.analyze(content)
            
            # Step 3: Save to Notion
            await processing_msg.edit_text("💾 Saving to Notion...")
            notion_url = await self.storage.save(content, analysis)
            
            # Step 4: Send result
            await self._send_result(update, content, analysis, notion_url)
            
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            await processing_msg.edit_text(
                f"❌ **Error processing your link**\n\n"
                f"`{str(e)[:200]}`\n\n"
                "Please try again or send a different link."
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
            f"✅ **Saved to Notion!**\n\n"
            f"{emoji} **{content.title[:80]}**\n"
            f"📂 `{analysis.category}` · ⭐ `{analysis.priority}`\n\n"
            f"**خلاصه فارسی:**\n{summary_fa}\n\n"
            f"**Summary:**\n{summary_en}\n\n"
        )
        
        if tags_text:
            message += f"🏷️ {tags_text}\n\n"
        
        if notion_url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Open in Notion", url=notion_url)],
                [InlineKeyboardButton("🔗 Original Link", url=content.url)]
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
        
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.start))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        await self.application.initialize()
        await self.application.start()
        
        # Use polling instead of webhook (works on localhost)
        await self.application.updater.start_polling(drop_pending_updates=True)
        
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
