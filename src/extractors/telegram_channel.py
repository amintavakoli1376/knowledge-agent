"""Telegram channel/public post content extractor."""
import re
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from .base import BaseExtractor
from ..models import ExtractedContent


class TelegramChannelExtractor(BaseExtractor):
    """Extract content from public Telegram channel posts."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'(?:t\.me|telegram\.me)/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        try:
            # Try t.me preview page (no login needed for public channels)
            text, author, title = await self._scrape_tme(url)
            
            if not text:
                return ExtractedContent(
                    title="Telegram Post",
                    full_text="Could not access this Telegram post. It may be from a private channel.",
                    url=url, platform='telegram',
                    metadata={'error': 'Private or inaccessible'}
                )
            
            return ExtractedContent(
                title=title or "Telegram Post",
                full_text=text[:8000],
                url=url,
                platform='telegram',
                author=author or '',
                metadata={}
            )
        except Exception as e:
            return ExtractedContent(
                title="Telegram Post",
                full_text=f"Could not extract: {str(e)}",
                url=url, platform='telegram',
                metadata={'error': str(e)}
            )
    
    async def _scrape_tme(self, url: str) -> tuple:
        """Scrape the t.me preview page for content."""
        try:
            r = await self.client.get(url)
            if r.status_code != 200:
                return None, None, None
            
            soup = BeautifulSoup(r.text, 'lxml')
            
            # Try different selectors for Telegram preview pages
            text = ""
            for selector in ['.tgme_widget_message_text', '.tgme_message_text',
                           '.tgme_widget_message_bubble', 'meta[property="og:description"]']:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(strip=True)
                    if text:
                        break
            
            # Meta description fallback
            if not text:
                meta = soup.find('meta', property='og:description') or \
                       soup.find('meta', attrs={'name': 'description'})
                if meta:
                    text = meta.get('content', '')
            
            # Author/channel name
            author = ""
            author_el = soup.select_one('.tgme_widget_message_owner_name') or \
                        soup.select_one('.tgme_channel_info_header_title')
            if author_el:
                author = author_el.get_text(strip=True)
            
            # Title from og:title or page title
            title = ""
            meta_title = soup.find('meta', property='og:title')
            if meta_title:
                title = meta_title.get('content', '')
            if not title and soup.title:
                title = soup.title.get_text(strip=True)[:100]
            
            # Clean text
            if text:
                text = re.sub(r'\s+', ' ', text).strip()
            
            return text, author, title
            
        except Exception:
            return None, None, None
    
    async def close(self):
        await self.client.aclose()
