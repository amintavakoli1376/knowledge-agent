"""Instagram post content extractor."""
import re
import json
from typing import Optional
import httpx

from .base import BaseExtractor
from ..models import ExtractedContent


class InstagramExtractor(BaseExtractor):
    """Extract content from Instagram posts."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'instagram\.com/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        # Handle both /p/ and /reel/ URLs
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            return ExtractedContent(
                title="Invalid Instagram URL",
                full_text="Could not extract post information.",
                url=url, platform='instagram',
                metadata={'error': 'Invalid URL'}
            )
        
        try:
            # Try oEmbed first (no login needed)
            text, author = await self._get_oembed(url)
            
            # Try Playwright for more details
            page_text = await self._try_playwright(url)
            
            full_text = page_text or text or f"Instagram post ({shortcode})"
            title = full_text[:100] if len(full_text) > 20 else f"Instagram post by {author or 'unknown'}"
            
            return ExtractedContent(
                title=title,
                full_text=full_text[:8000],
                url=url,
                platform='instagram',
                author=author or '',
                metadata={
                    'shortcode': shortcode,
                }
            )
        except Exception as e:
            return ExtractedContent(
                title=f"Instagram post ({shortcode})",
                full_text=f"Content temporarily unavailable.",
                url=url, platform='instagram',
                metadata={'shortcode': shortcode, 'error': str(e)}
            )
    
    def _extract_shortcode(self, url: str) -> Optional[str]:
        match = re.search(r'instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_-]+)', url)
        return match.group(1) if match else None
    
    async def _get_oembed(self, url: str) -> tuple:
        """Get basic info via oEmbed."""
        try:
            api_url = f"https://api.instagram.com/oembed?url={url}"
            r = await self.client.get(api_url)
            if r.status_code == 200:
                data = r.json()
                title = data.get('title', '')
                author = data.get('author_name', '')
                # Clean HTML from title
                title = re.sub(r'<[^>]+>', '', title)
                return title, author
        except:
            pass
        return None, None
    
    async def _try_playwright(self, url: str) -> Optional[str]:
        """Try extracting description via Playwright."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3000)
                    
                    text = await page.evaluate('''() => {
                        const meta = document.querySelector('meta[property="og:description"]');
                        if (meta) return meta.content;
                        return document.title || '';
                    }''')
                    await browser.close()
                    return text
                except:
                    await browser.close()
                    return None
        except ImportError:
            return None
    
    async def close(self):
        await self.client.aclose()
