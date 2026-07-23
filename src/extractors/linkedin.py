"""LinkedIn post content extractor.

Extracts post text and metadata from LinkedIn posts.
Uses Playwright for JavaScript-rendered content.
"""
import re
from typing import Optional

from .base import BaseExtractor
from ..models import ExtractedContent


class LinkedInExtractor(BaseExtractor):
    """Extract content from LinkedIn posts."""
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'linkedin\.com/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        """Extract content from a LinkedIn post URL."""
        try:
            # Try simple approach first — extract from URL patterns
            # LinkedIn post URLs contain the post text in the URL sometimes
            text_from_url = self._extract_text_from_url(url)
            
            # Try Playwright for full page content
            page_text = await self._try_playwright(url)
            
            full_text = page_text or text_from_url or "LinkedIn post content unavailable."
            
            return ExtractedContent(
                title=self._generate_title(full_text),
                full_text=full_text[:8000],
                url=url,
                platform='linkedin',
                metadata={
                    'note': 'LinkedIn extraction uses visible text content',
                }
            )
        except Exception as e:
            return ExtractedContent(
                title=f"LinkedIn Error",
                full_text=f"Could not extract LinkedIn post: {str(e)}",
                url=url, platform='linkedin',
                metadata={'error': str(e)}
            )
    
    def _extract_text_from_url(self, url: str) -> Optional[str]:
        """Extract any useful text from LinkedIn post URL patterns."""
        # LinkedIn posts sometimes have URN-encoded text
        match = re.search(r'activity-(\d+)', url)
        if match:
            return f"LinkedIn Activity ID: {match.group(1)}"
        
        match = re.search(r'/posts/([^/]+)', url)
        if match:
            post_slug = match.group(1).replace('-', ' ').replace('_', ' ')
            return post_slug
        
        return None
    
    async def _try_playwright(self, url: str) -> Optional[str]:
        """Try to extract content using Playwright."""
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                page = await context.new_page()
                
                try:
                    await page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3000)
                    
                    # Try to get visible text
                    text = await page.evaluate('''
                        () => {
                            const selectors = [
                                '.feed-shared-update-v2__description',
                                '.update-components-text',
                                '.break-words',
                                'article p',
                                '[data-test-id="main-feed-activity-card"]',
                                'main p',
                            ];
                            for (const sel of selectors) {
                                const el = document.querySelector(sel);
                                if (el && el.textContent.trim().length > 20) {
                                    return el.textContent.trim();
                                }
                            }
                            // Fallback: all paragraphs
                            const paragraphs = Array.from(document.querySelectorAll('p'));
                            return paragraphs.map(p => p.textContent).filter(t => t.trim()).join('\\n').slice(0, 5000);
                        }
                    ''')
                    
                    await browser.close()
                    
                    if text and len(text.strip()) > 20:
                        return text.strip()
                        
                except Exception:
                    await browser.close()
                    return None
                    
        except ImportError:
            # Playwright not installed
            return None
        except Exception:
            return None
    
    def _generate_title(self, text: str) -> str:
        """Generate a title from LinkedIn post text."""
        # Take first meaningful line
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in lines:
            if len(line) > 20:
                return line[:100]
        return f"LinkedIn Post ({text[:50]}...)"
