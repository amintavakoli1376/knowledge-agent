"""General website content extractor using Playwright + BeautifulSoup."""
import httpx
from bs4 import BeautifulSoup
from typing import Optional

from .base import BaseExtractor
from ..models import ExtractedContent
from ..utils.url_parser import detect_platform


class WebsiteExtractor(BaseExtractor):
    """Extract content from any website using HTTP + BeautifulSoup."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
    
    def can_handle(self, url: str) -> bool:
        platform = detect_platform(url)
        return platform in ('website', 'medium', 'github')
    
    async def extract(self, url: str) -> ExtractedContent:
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            html = response.text
            
            soup = BeautifulSoup(html, 'lxml')
            
            # Extract title
            title = ""
            if soup.title:
                title = soup.title.get_text(strip=True)
            
            # Extract meta description
            meta_desc = ""
            meta_tag = soup.find('meta', attrs={'name': 'description'}) or \
                       soup.find('meta', attrs={'property': 'og:description'})
            if meta_tag:
                meta_desc = meta_tag.get('content', '')
            
            # Extract main content (article tags, main tag, or body)
            main_content = ""
            for tag in ['article', 'main', '[role=main]', '.post-content', '.article-content']:
                element = soup.select_one(tag)
                if element:
                    main_content = element.get_text(strip=True, separator='\n')
                    break
            
            if not main_content:
                # Fallback: get all paragraph text
                paragraphs = soup.find_all('p')
                main_content = '\n'.join(p.get_text(strip=True) for p in paragraphs[:20])
            
            # Clean up
            main_content = self._clean_text(main_content)
            
            return ExtractedContent(
                title=title or url,
                full_text=main_content[:10000],  # cap at 10K chars
                url=url,
                platform='website',
                metadata={
                    'description': meta_desc,
                    'domain': url.split('/')[2] if '//' in url else url,
                }
            )
        except Exception as e:
            return ExtractedContent(
                title=f"Error: {url}",
                full_text=f"Failed to extract content: {str(e)}",
                url=url,
                platform='website',
                metadata={'error': str(e)}
            )
    
    def _clean_text(self, text: str) -> str:
        """Clean extracted text."""
        import re
        # Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {3,}', ' ', text)
        return text.strip()[:10000]
    
    async def close(self):
        await self.client.aclose()
