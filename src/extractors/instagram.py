"""Instagram post content extractor with fallback strategies."""
import re
import json
import os
import tempfile
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from .base import BaseExtractor
from ..models import ExtractedContent


class InstagramExtractor(BaseExtractor):
    """Extract content from Instagram posts.
    
    Uses multiple strategies (oEmbed, Playwright, public endpoints).
    Supports OCR for carousel slides when images are accessible.
    """
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'instagram\.com/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            return ExtractedContent(
                title="Invalid Instagram URL",
                full_text="Instagram post - could not extract information.",
                url=url, platform='instagram',
                metadata={'error': 'Invalid URL'}
            )
        
        temp_files = []
        try:
            # Track what worked
            extracted_text = ""
            author = ""
            image_urls = []
            
            # Strategy 1: oEmbed API
            caption, oembed_author, _ = await self._get_oembed(url)
            if caption:
                extracted_text += f"📝 Caption:\n{caption}\n\n"
                author = oembed_author or author
            
            # Strategy 2: Playwright with full JS rendering
            if not extracted_text:
                page_data = await self._try_playwright_full(url)
                if page_data:
                    if page_data.get('caption'):
                        extracted_text += f"📝 Caption:\n{page_data['caption']}\n\n"
                    if page_data.get('author'):
                        author = page_data['author']
                    image_urls = page_data.get('images', [])
            
            # Strategy 3: Playwright basic fallback
            if not extracted_text:
                basic_text, basic_images = await self._try_playwright_basic(url)
                if basic_text:
                    extracted_text += basic_text + "\n\n"
                if basic_images:
                    image_urls = basic_images or image_urls
            
            # OCR on images if we found any
            ocr_texts = []
            if image_urls:
                for i, img_url in enumerate(image_urls[:10]):  # Max 10 slides
                    result = await self._ocr_image(img_url)
                    if result and result.get('text'):
                        ocr_texts.append(f"📸 Slide {i+1}:\n{result['text']}")
                        temp_files.extend(result.get('temp_files', []))
            
            if ocr_texts:
                extracted_text += "\n".join(ocr_texts)
            
            # Prepare final output
            if not extracted_text:
                extracted_text = (
                    f"Instagram carousel post ({shortcode}). "
                    f"Instagram has blocked automated content extraction. "
                    f"Contains {len(image_urls)} slide(s) with visual content."
                )
                if not image_urls:
                    extracted_text = (
                        f"Instagram post ({shortcode}). "
                        f"Could not extract content - Instagram requires login."
                    )
            
            title = "Instagram post"
            lines = extracted_text.split('\n')
            for line in lines:
                line = line.strip()
                if line and not line.startswith('📝') and not line.startswith('📸') and len(line) > 15:
                    title = line[:100]
                    break
            
            meta = {'shortcode': shortcode}
            if len(image_urls) > 1:
                meta['carousel_slides'] = len(image_urls)
            if ocr_texts:
                meta['ocr_processed'] = len(ocr_texts)
            
            return ExtractedContent(
                title=title,
                full_text=extracted_text[:12000],
                url=url,
                platform='instagram',
                author=author or shortcode,
                metadata=meta
            )
            
        except Exception as e:
            return ExtractedContent(
                title=f"Instagram post ({shortcode})",
                full_text=f"Instagram content temporarily unavailable.",
                url=url, platform='instagram',
                metadata={'shortcode': shortcode, 'error': str(e)}
            )
        finally:
            for f in temp_files:
                try:
                    if os.path.exists(f): os.unlink(f)
                except: pass
    
    def _extract_shortcode(self, url: str) -> Optional[str]:
        match = re.search(r'instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_-]+)', url)
        return match.group(1) if match else None
    
    async def _get_oembed(self, url: str) -> tuple:
        """Strategy 1: oEmbed API (no login, but often blocked)."""
        try:
            r = await self.client.get(
                f"https://api.instagram.com/oembed?url={url}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json()
                html_content = data.get('html', '')
                title = data.get('title', '')
                author = data.get('author_name', '')
                
                caption = ""
                if html_content:
                    soup = BeautifulSoup(html_content, 'lxml')
                    caption = soup.get_text(separator='\n', strip=True)
                if not caption and title:
                    caption = re.sub(r'<[^>]+>', '', title)
                
                return caption, author, 0
        except: pass
        return None, None, 0
    
    async def _try_playwright_full(self, url: str) -> Optional[dict]:
        """Strategy 2: Playwright with full JS rendering."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, timeout=20000, wait_until='networkidle')
                    await page.wait_for_timeout(5000)
                    
                    data = await page.evaluate('''() => {
                        const result = { caption: '', author: '', images: [] };
                        
                        // Try to get __INITIAL_STATE__ from page
                        try {
                            const script = document.querySelector('script[type="text/javascript"]');
                            if (script && script.textContent.includes('__INITIAL_STATE__')) {
                                const match = script.textContent.match(/window\\.__INITIAL_STATE__\\s*=\\s*({.+?});/);
                                if (match) {
                                    const state = JSON.parse(match[1]);
                                    const media = state?.items?.[0] || state?.media?.[0];
                                    if (media) {
                                        result.caption = media.caption?.text || '';
                                        result.author = media.user?.username || media.owner?.username || '';
                                        if (media.carousel_media) {
                                            result.images = media.carousel_media.map(m => m.image_versions2?.candidates?.[0]?.url || m.display_url || '').filter(Boolean);
                                        } else {
                                            result.images = [media.display_url || media.image_versions2?.candidates?.[0]?.url || ''].filter(Boolean);
                                        }
                                    }
                                }
                            }
                        } catch(e) {}
                        
                        // Fallback: meta tags
                        if (!result.caption) {
                            const meta = document.querySelector('meta[property="og:description"]');
                            if (meta) result.caption = meta.content;
                        }
                        if (!result.author) {
                            const meta = document.querySelector('meta[property="og:title"]');
                            if (meta) result.author = meta.content;
                        }
                        if (!result.images.length) {
                            const meta = document.querySelector('meta[property="og:image"]');
                            if (meta) result.images = [meta.content];
                        }
                        
                        return result;
                    }''')
                    
                    await browser.close()
                    return data
                    
                except Exception:
                    await browser.close()
                    return None
        except ImportError:
            return None
    
    async def _try_playwright_basic(self, url: str) -> tuple:
        """Strategy 3: Basic Playwright with just meta tags."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3000)
                    
                    result = await page.evaluate('''() => {
                        const metas = document.querySelector('meta[property="og:description"]');
                        const img = document.querySelector('meta[property="og:image"]');
                        const imgs = [];
                        if (img) imgs.push(img.content);
                        return {
                            text: metas ? metas.content : (document.title || ''),
                            images: imgs
                        };
                    }''')
                    
                    await browser.close()
                    return (result.get('text'), result.get('images', [])) if result else (None, [])
                    
                except Exception:
                    await browser.close()
                    return (None, [])
        except ImportError:
            return (None, [])
    
    async def _ocr_image(self, img_url: str) -> Optional[dict]:
        """Download image and run OCR. Auto-cleans temp files."""
        temp_path = None
        try:
            r = await self.client.get(img_url, timeout=20.0)
            if r.status_code != 200:
                return None
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
                tmp.write(r.content)
                temp_path = tmp.name
            
            import subprocess
            result = subprocess.run(
                ['tesseract', temp_path, 'stdout', '-l', 'eng+fas', '--psm', '6'],
                capture_output=True, text=True, timeout=30
            )
            
            text = result.stdout.strip() if result.stdout else None
            
            # Clean up immediately
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            
            return {'text': text, 'temp_files': []}
            
        except Exception:
            if temp_path and os.path.exists(temp_path):
                try: os.unlink(temp_path)
                except: pass
            return None
    
    async def close(self):
        await self.client.aclose()
