"""Instagram post content extractor with OCR for carousel slides.

Supports single posts, carousels (multi-slide), and reels.
Downloads images, runs OCR, and extracts text from each slide.
All images are deleted after processing.
"""
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
    """Extract content from Instagram posts including carousel slides via OCR."""
    
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
        
        temp_files = []  # Track temp files for cleanup
        try:
            # Step 1: Get caption + metadata from oEmbed
            caption, author, slide_count = await self._get_oembed(url)
            
            # Step 2: Get image URLs via Playwright
            image_urls, page_text = await self._get_images_and_text(url)
            
            # Step 3: Download images and run OCR on each
            ocr_texts = []
            if image_urls:
                for img_url in image_urls:
                    ocr_result = await self._ocr_image(img_url)
                    if ocr_result:
                        ocr_texts.append(ocr_result)
                        temp_files.extend(ocr_result.get('temp_files', []))
            
            # Step 4: Combine all text sources
            parts = []
            if caption:
                parts.append(f"📝 Caption:\n{caption}")
            
            if ocr_texts:
                for i, ot in enumerate(ocr_texts):
                    slide_text = ot.get('text', '')
                    if slide_text:
                        parts.append(f"📸 Slide {i+1}:\n{slide_text}")
            
            if page_text:
                parts.append(f"ℹ️ Meta: {page_text}")
            
            full_text = "\n\n".join(parts) if parts else f"Instagram post ({shortcode})"
            
            # Better title from caption
            lines = caption.split('\n') if caption else []
            title = lines[0][:100] if lines and len(lines[0]) > 10 else f"Instagram by {author or shortcode}"
            
            # Build metadata
            meta = {'shortcode': shortcode}
            if len(ocr_texts) > 1:
                meta['carousel_slides'] = len(ocr_texts)
                meta['ocr_processed'] = True
            elif image_urls and len(ocr_texts) == 0:
                meta['images_found'] = len(image_urls)
                meta['ocr_failed'] = True
            
            return ExtractedContent(
                title=title,
                full_text=full_text[:12000],
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
        finally:
            # Clean up all temp files
            for f in temp_files:
                try:
                    if os.path.exists(f):
                        os.unlink(f)
                except:
                    pass
    
    def _extract_shortcode(self, url: str) -> Optional[str]:
        match = re.search(r'instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_-]+)', url)
        return match.group(1) if match else None
    
    async def _get_oembed(self, url: str) -> tuple:
        """Get post info via oEmbed API."""
        try:
            api_url = f"https://api.instagram.com/oembed?url={url}"
            r = await self.client.get(api_url)
            if r.status_code != 200:
                return None, None, 0
            
            data = r.json()
            author = data.get('author_name', '')
            html_content = data.get('html', '')
            title = data.get('title', '')
            
            # Extract caption from HTML
            caption = ""
            if html_content:
                soup = BeautifulSoup(html_content, 'lxml')
                caption = soup.get_text(separator='\n', strip=True)
            if not caption and title:
                caption = re.sub(r'<[^>]+>', '', title)
            
            # Count images in HTML for slide indication
            slide_count = len(re.findall(r'<img', html_content)) if html_content else 0
            
            return caption, author, slide_count
            
        except Exception:
            return None, None, 0
    
    async def _get_images_and_text(self, url: str) -> tuple:
        """Get image URLs and meta text via Playwright."""
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3000)
                    
                    result = await page.evaluate('''() => {
                        // Get all image URLs from the page
                        const images = [];
                        const metas = [];
                        
                        // Try to find carousel images in JSON-LD
                        const ld = document.querySelector('script[type="application/ld+json"]');
                        if (ld) {
                            try {
                                const data = JSON.parse(ld.textContent);
                                // Carousel images
                                if (data.carousel && Array.isArray(data.carousel)) {
                                    data.carousel.forEach(item => {
                                        if (item.url) images.push(item.url);
                                    });
                                }
                                // Single image
                                if (data.url && !images.length) images.push(data.url);
                                // Caption
                                if (data.caption) metas.push(data.caption);
                                if (data.description) metas.push(data.description);
                            } catch(e) {}
                        }
                        
                        // Fallback: find og:image
                        if (!images.length) {
                            const og = document.querySelector('meta[property="og:image"]');
                            if (og) images.push(og.content);
                        }
                        
                        // Meta description
                        const metaDesc = document.querySelector('meta[property="og:description"]');
                        if (metaDesc) metas.push(metaDesc.content);
                        
                        return { images, metas };
                    }''')
                    
                    image_urls = result.get('images', []) if result else []
                    meta_text = '\n'.join(result.get('metas', [])) if result else ''
                    
                    await browser.close()
                    return image_urls, meta_text.strip() or None
                    
                except Exception:
                    await browser.close()
                    return [], None
                    
        except ImportError:
            return [], None
        except Exception:
            return [], None
    
    async def _ocr_image(self, img_url: str) -> Optional[dict]:
        """Download an image and run OCR on it. Cleans up after."""
        temp_path = None
        try:
            # Download image
            r = await self.client.get(img_url, timeout=20.0)
            if r.status_code != 200:
                return None
            
            # Save to temp file
            ext = '.jpg'
            if 'content-type' in r.headers:
                ct = r.headers['content-type']
                if 'png' in ct: ext = '.png'
                elif 'webp' in ct: ext = '.webp'
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(r.content)
                temp_path = tmp.name
            
            # Run Tesseract OCR (supports English + Persian)
            import subprocess
            result = subprocess.run(
                ['tesseract', temp_path, 'stdout', '-l', 'eng+fas', '--psm', '6'],
                capture_output=True, text=True, timeout=30
            )
            
            text = result.stdout.strip() if result.stdout else None
            
            return {
                'text': text,
                'temp_files': [temp_path] if temp_path else [],
            }
            
        except Exception as e:
            # Clean up on error
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            return None
    
    async def close(self):
        await self.client.aclose()
