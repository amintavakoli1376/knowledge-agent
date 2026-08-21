"""Telegram Bot gateway."""
import asyncio
import json
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from ..config import settings
from ..utils.url_parser import detect_platform, is_valid_url
from ..extractors.website import WebsiteExtractor
from ..extractors.arxiv import ArxivExtractor
from ..processors.summarizer import ContentSummarizer
from ..storage.notion import NotionStorage
from ..storage import sqlite_store
from ..models import ExtractedContent, AIAnalysis
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
    
    def _main_menu_keyboard(self) -> InlineKeyboardMarkup:
        """کيبورد منوی اصلی (انتخاب حالت)."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🧠 گفتگو با دانش", callback_data="mode:ask")],
            [InlineKeyboardButton("💾 ذخیره مطلب", callback_data="mode:save")],
        ])

    @staticmethod
    def _back_to_menu_keyboard() -> InlineKeyboardMarkup:
        """کيبورد دکمه برگشت به منوی اصلی."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu:main")],
        ])

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command with inline mode buttons."""
        await update.message.reply_text(
            "👋 **به دستیار دانش خوش آمدید!**\n\n"
            "یکی از حالت‌ها را انتخاب کنید:\n\n"
            "🧠 **گفتگو با دانش** — سؤالتان را بپرسید، از پایگاه دانش جواب می‌گیرید.\n"
            "💾 **ذخیره مطلب** — لینک یا پست بفرستید تا خلاصه و در Notion ذخیره شود.\n\n"
            "بعد از انتخاب، هر پیامی که بفرستید در همان حالت پردازش می‌شود.",
            reply_markup=self._main_menu_keyboard(),
        )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard button presses (mode selection)."""
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""
        user_id = query.from_user.id if query.from_user else None

        if data == "mode:ask":
            if user_id is not None:
                context.user_data["mode"] = "ask"
            await query.message.reply_text(
                "🧠 **حالت گفتگو با دانش فعال شد**\n\n"
                "سؤالتان را بنویسید — از پایگاه دانش جواب می‌گیرید.\n"
                "مثلاً: «بهترین مقاله درباره RAG چیست؟»\n\n"
                "🔙 برای بازگشت به منو، دکمه پایین را بزنید.",
                reply_markup=self._back_to_menu_keyboard(),
            )
        elif data == "mode:save":
            if user_id is not None:
                context.user_data["mode"] = "save"
            await query.message.reply_text(
                "💾 **حالت ذخیره مطلب فعال شد**\n\n"
                "لینک یا پست تلگرام بفرستید تا خلاصه‌سازی و ذخیره شود.\n\n"
                "🔙 برای بازگشت به منو، دکمه پایین را بزنید.",
                reply_markup=self._back_to_menu_keyboard(),
            )
        elif data == "menu:main":
            # بازگشت به منوی اصلی — حالت فعلی پاک می‌شود
            if user_id is not None:
                context.user_data.pop("mode", None)
            await query.message.reply_text(
                "👋 **به دستیار دانش خوش آمدید!**\n\n"
                "یکی از حالت‌ها را انتخاب کنید:\n\n"
                "🧠 **گفتگو با دانش** — سؤالتان را بپرسید، از پایگاه دانش جواب می‌گیرید.\n"
                "💾 **ذخیره مطلب** — لینک یا پست بفرستید تا خلاصه و در Notion ذخیره شود.\n\n"
                "بعد از انتخاب، هر پیامی که بفرستید در همان حالت پردازش می‌شود.",
                reply_markup=self._main_menu_keyboard(),
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
        """Handle incoming messages: links/raw posts (saved) OR questions (RAG /ask) OR files."""
        if not update.message:
            return
        
        # 📄 فایل ارسالی (PDF و امثالش) → مستقیم پردازش و ذخیره
        if update.message.document:
            await self._process_document(update, context)
            return
        
        # استخراج متن از پیام یا کپشن فوروارد
        text = (update.message.text or update.message.caption or "").strip()
        if not text:
            return
        
        # اگر پیام با /ask شروع شد → RAG
        if text.lower().startswith("/ask"):
            question = re.sub(r"^/ask\s*", "", text, flags=re.IGNORECASE).strip()
            if question:
                await self._ask_rag(update, question)
                return
            await update.message.reply_text(
                "🧠 فرمت: `/ask سوال شما`\nمثلاً: `/ask بهترین مقاله درباره RAG چیست؟`"
            )
            return
        
        # اگر کاربر حالت "ask" را انتخاب کرده → سوال در نظر بگیر
        mode = context.user_data.get("mode")
        if mode == "ask":
            await self._ask_rag(update, text)
            return
        
        # پیدا کردن لینک t.me به عنوان «لینک پست» (اگه هست)
        tme_match = re.search(r'https?://t\.me/[^\s]+', text)
        post_url = tme_match.group(0) if tme_match else ""
        
        # لینک واقعی هست → پردازش/ذخیره
        url = extract_first_url(text)
        real_link = url and not url.startswith("https://t.me") and not url.startswith("http://t.me")
        
        if real_link and not post_url:
            # ذخیره
            await self._process_link(update, url)
            return
        
        # پست تلگرام (فوروارد یا متن با t.me) → ذخیره
        if post_url or real_link or tme_match:
            await self._process_raw_post(update, text, post_url)
            return
        
        # هیچ لینکی نیست:
        # - اگر حالت "save" انتخاب شده → به عنوان متن ذخیره کن
        # - وگرنه پیش‌فرض: سوال RAG
        if mode == "save":
            await self._process_raw_post(update, text, "")
        else:
            await self._ask_rag(update, text)
    
    async def _ask_rag(self, update: Update, question: str):
        """RAG: retrieve from knowledge base and answer."""
        processing = await update.message.reply_text("🧠 در حال جستجو در پایگاه دانش...")
        try:
            engine = RagEngine()
            answer = await engine.ask(question)
            # دکمه برگشت به منو همیشه چسبیده به جواب
            answer += "\n\n_برای تغییر حالت، از منو استفاده کنید._"
            await processing.edit_text(answer, reply_markup=self._back_to_menu_keyboard())
        except Exception as e:
            logger.error(f"RAG ask failed: {e}")
            await processing.edit_text(
                f"❌ **خطا در جستجوی دانش**\n\n`{str(e)[:200]}`",
                reply_markup=self._back_to_menu_keyboard(),
            )
    
    async def _process_link(self, update: Update, url: str):
        """پردازش لینک (استخراج + خلاصه + ذخیره)."""
        # ⚡ چک تکراری: اگه قبلاً ذخیره شده، سریع از DB نمایش بده
        existing = sqlite_store.get_by_url(url)
        if existing and existing.get("summary_fa"):
            msg = await update.message.reply_text("⚡ این لینک قبلاً ذخیره شده — خلاصه از دیتابیس:")
            content = ExtractedContent(
                url=url,
                title=existing.get("title") or url,
                full_text=existing.get("full_text") or "",
                platform=existing.get("platform") or "website",
            )
            analysis = AIAnalysis(
                summary_fa=existing.get("summary_fa") or "",
                summary_en=existing.get("summary_en") or "",
                key_points=json.loads(existing.get("key_points") or "[]"),
                category=existing.get("category") or "General",
                tags=json.loads(existing.get("tags") or "[]"),
                priority=existing.get("priority") or "Medium",
            )
            notion_url = ""  # از Notion دوباره ذخیره نمی‌کنیم
            await self._send_result(update, content, analysis, notion_url)
            return

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
            
            await processing_msg.edit_text("🤖 در حال تحلیل با هوش مصنوعی...")
            analysis = await self.summarizer.analyze(content)
            sqlite_store.save_analysis(url, analysis)
            
            await processing_msg.edit_text("💾 در حال ذخیره در Notion...")
            notion_url = await self.storage.save(content, analysis)
            
            await self._send_result(update, content, analysis, notion_url)
            
            # ⚡ embedding بعد از جواب (بدون معطلی کاربر)
            asyncio.create_task(self._embed_content(content))
            
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
    
    async def _process_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پردازش فایل ارسالی (PDF و ...): دانلود → استخراج متن → خلاصه → ذخیره."""
        doc = update.message.document
        file_name = doc.file_name or "document"
        mime = (doc.mime_type or "").lower()

        processing_msg = await update.message.reply_text(
            f"📄 در حال دریافت فایل `{file_name}`..."
        )
        try:
            if "pdf" not in mime and not file_name.lower().endswith(".pdf"):
                await processing_msg.edit_text(
                    "❌ فقط فایل‌های PDF پشتیبانی می‌شوند. لطفاً یک PDF بفرستید."
                )
                return

            # دانلود فایل از تلگرام
            file = await context.bot.get_file(doc.file_id)
            import tempfile, os
            tmp_path = None
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

            # استخراج متن با PyMuPDF
            import fitz
            full_text = ""
            title = ""
            pdf = fitz.open(tmp_path)
            for page_num in range(len(pdf)):
                page = pdf[page_num]
                text = page.get_text().strip()
                if page_num == 0 and text:
                    lines = text.split("\n")
                    title = lines[0][:200] if lines else file_name
                full_text += text + "\n\n"
            pages = len(pdf)
            pdf.close()

            # پاک‌سازی فایل موقت
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

            if not full_text.strip():
                raise ValueError("این PDF متنی استخراج‌شدنی ندارد (شاید اسکن‌شده باشد).")

            title = title or file_name.replace(".pdf", "").replace("_", " ")[:100]

            content = ExtractedContent(
                url="",
                title=title,
                full_text=full_text[:10000],
                platform="pdf",
                metadata={"pages": pages, "source": "telegram-upload", "file_name": file_name},
            )

            # چک تکراری بر اساس اسم فایل + ذخیره
            sqlite_store.save_content(content)

            await processing_msg.edit_text("🤖 در حال تحلیل با هوش مصنوعی...")
            analysis = await self.summarizer.analyze(content)
            sqlite_store.save_analysis(content.url, analysis)

            await processing_msg.edit_text("💾 در حال ذخیره در Notion...")
            notion_url = await self.storage.save(content, analysis)

            await self._send_result(update, content, analysis, notion_url)

            # ⚡ embedding بعد از جواب
            asyncio.create_task(self._embed_content(content))

        except Exception as e:
            logger.error(f"Error processing document: {e}")
            await processing_msg.edit_text(
                f"❌ **خطا در پردازش فایل**\n\n`{str(e)[:200]}`"
            )

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
            
            await processing_msg.edit_text("🤖 در حال تحلیل با هوش مصنوعی...")
            analysis = await self.summarizer.analyze(content)
            sqlite_store.save_analysis(post_url or text, analysis)
            
            await processing_msg.edit_text("💾 در حال ذخیره در Notion...")
            notion_url = await self.storage.save(content, analysis)
            
            await self._send_result(update, content, analysis, notion_url)
            
            # ⚡ embedding بعد از جواب (بدون معطلی کاربر)
            asyncio.create_task(self._embed_content(content))
            
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
            kb_rows = [
                [InlineKeyboardButton("🌐 مشاهده در Knowledge Base (بدون لاگین)", url=notion_url)],
            ]
            if content.url:
                kb_rows.append([InlineKeyboardButton("🔗 لینک اصلی", url=content.url)])
            kb_rows.append([InlineKeyboardButton("🧠 گفتگو با دانش", callback_data="mode:ask")])
            kb_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu:main")])
            keyboard = InlineKeyboardMarkup(kb_rows)
            await update.message.reply_text(message, reply_markup=keyboard)
        else:
            kb_rows = []
            if content.url:
                kb_rows.append([InlineKeyboardButton("🔗 لینک اصلی", url=content.url)])
            kb_rows.append([InlineKeyboardButton("🧠 گفتگو با دانش", callback_data="mode:ask")])
            kb_rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu:main")])
            await update.message.reply_text(
                message + "\n*(Notion API not configured)*" if not notion_url else message,
                reply_markup=InlineKeyboardMarkup(kb_rows),
            )
    
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
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(
            MessageHandler(
                (filters.TEXT | filters.CAPTION | filters.ATTACHMENT) & ~filters.COMMAND,
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