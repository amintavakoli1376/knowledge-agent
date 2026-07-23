"""Instagram post content extractor.
Supports single posts, carousels (multi-slide), and reels.
"""
import re
import json
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from .base import BaseExtractor
from ..models import ExtractedContent


class InstagramExtractor(BaseExtractor):
    """Extract content from Instagram posts."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'instagram\.com/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            return ExtractedContent(
                title="Invalid Instagram URL",
                full_text="Could not extract post information.",
                url=url, platform='instagram',
                metadata={'error': 'Invalid URL'}
            )
        
        try:
            # Strategy 1: Try oEmbed API (returns caption text + author)
            oembed_text, author, slide_count = await self._get_oembed(url)
            
            # Strategy 2: Try Playwright for OG metadata
            page_text = await self._try_playwright(url)
            
            # Combine all extracted text
            all_texts = [t for t in [oembed_text, page_text] if t]
            full_text = "\n\n".join(all_texts) if all_texts else f"Instagram post ({shortcode})"
            
            # Better title: use first line of caption
            lines = full_text.split('\n')
            title = lines[0][:100] if lines and len(lines[0]) > 10 else f"Instagram by {author or shortcode}"
            
            meta = {'shortcode': shortcode}
            if slide_count and slide_count > 1:
                meta['carousel_slides'] = slide_count
                meta['note'] = 'This is a carousel post with multiple slides. '
                'Only the caption text was extracted. '
                'Visual content of each slide requires image analysis.'
            
            return ExtractedContent(
                title=title,
                full_text=full_text[:8000],
                url=url,
                platform='instagram',
                author=author or '',
                metadata=meta
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
        """Get post info via oEmbed API.
        Returns: (caption_text, author_name, slide_count)
        """
        try:
            api_url = f"https://api.instagram.com/oembed?url={url}"
            r = await self.client.get(api_url)
            if r.status_code != 200:
                return None, None, None
            
            data = r.json()
            author = data.get('author_name', '')
            
            # The oEmbed returns HTML with the caption embedded
            html_content = data.get('html', '')
            title = data.get('title', '')
            
            # Extract caption from the HTML using BeautifulSoup
            caption = ""
            if html_content:
                soup = BeautifulSoup(html_content, 'lxml')
                caption = soup.get_text(separator='\n', strip=True)
            
            # Also try the title field (often contains caption)
            if not caption and title:
                caption = re.sub(r'<[^>]+>', '', title)
            
            # Detect carousel from HTML (look for multiple images)
            slide_count = 0
            if html_content:
                # Count image/slide indicators in the oEmbed HTML
                img_count = len(re.findall(r'<img', html_content))
                if img_count > 1:
                    slide_count = img_count
            
            return caption, author, slide_count
            
        except Exception:
            return None, None, None
    
    async def _try_playwright(self, url: str) -> Optional[str]:
        """Try extracting description and slide info via Playwright."""
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3000)
                    
                    text = await page.evaluate('''() => {
                        // Try multiple sources for text content
                        const sources = [];
                        
                        // Meta description
                        const meta = document.querySelector('meta[property="og:description"]');
                        if (meta && meta.content) sources.push(meta.content);
                        
                        // JSON-LD structured data
                        const ld = document.querySelector('script[type="application/ld+json"]');
                        if (ld) {
                            try {
                                const data = JSON.parse(ld.textContent);
                                if (data.caption) sources.push(data.caption);
                                if (data.description) sources.push(data.description);
                                if (data.articleBody) sources.push(data.articleBody);
                            } catch(e) {}
                        }
                        
                        // Page title
                        if (document.title) sources.push(document.title);
                        
                        return sources.join('\\n\\n');
                    }''')
                    
                    await browser.close()
                    return text.strip() if text and text.strip() else None
                    
                except Exception:
                    await browser.close()
                    return None
                    
        except ImportError:
            return None
        except Exception:
            return None
    
    async def close(self):
        await self.client.aclose()
