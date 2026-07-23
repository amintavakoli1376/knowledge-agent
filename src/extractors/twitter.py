"""Twitter/X post content extractor."""
import re
from typing import Optional
import httpx

from .base import BaseExtractor
from ..models import ExtractedContent


class TwitterExtractor(BaseExtractor):
    """Extract content from Twitter/X posts and threads."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=15.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"}
        )
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'(?:twitter|x)\.com/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        try:
            tweet_id = self._extract_tweet_id(url)
            
            # Try oEmbed API (no key needed)
            text, author = await self._get_oembed(url)
            
            if not text:
                text = f"Twitter/X post (ID: {tweet_id or 'unknown'})"
            
            return ExtractedContent(
                title=self._generate_title(text),
                full_text=text[:5000],
                url=url,
                platform='twitter',
                author=author or '',
                metadata={
                    'tweet_id': tweet_id,
                }
            )
        except Exception as e:
            return ExtractedContent(
                title="Twitter/X Post",
                full_text=f"Could not extract tweet: {str(e)}",
                url=url, platform='twitter',
                metadata={'error': str(e)}
            )
    
    def _extract_tweet_id(self, url: str) -> Optional[str]:
        match = re.search(r'(?:twitter|x)\.com/\w+/status/(\d+)', url)
        return match.group(1) if match else None
    
    async def _get_oembed(self, url: str) -> tuple:
        """Get tweet text via oEmbed."""
        try:
            oembed_url = f"https://publish.twitter.com/oembed?url={url}&omit_script=1"
            r = await self.client.get(oembed_url)
            if r.status_code == 200:
                data = r.json()
                html = data.get('html', '')
                # Strip HTML tags to get text
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                author = data.get('author_name', '')
                return text, author
        except:
            pass
        return None, None
    
    def _generate_title(self, text: str) -> str:
        return text[:100] if text else "Twitter/X Post"
    
    async def close(self):
        await self.client.aclose()
