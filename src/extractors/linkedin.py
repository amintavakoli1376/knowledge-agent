"""LinkedIn post content extractor.

Extracts post text and metadata from LinkedIn posts.
Uses LinkedIn's public embed endpoint (works without login).
"""
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseExtractor
from ..models import ExtractedContent


class LinkedInExtractor(BaseExtractor):
    """Extract content from LinkedIn posts."""
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'linkedin\.com/', url, re.IGNORECASE))
    
    async def extract(self, url: str) -> ExtractedContent:
        """Extract content from a LinkedIn post URL."""
        try:
            # 0. Try document transcript first (full PDF/PPT text, no login)
            doc_text = await self._extract_document_transcript(url)
            if doc_text:
                return ExtractedContent(
                    title=self._generate_title(doc_text),
                    full_text=doc_text,
                    url=url,
                    platform='linkedin',
                    metadata={'method': 'document-transcript'}
                )

            # 1. Try the public embed endpoint next (no login required)
            embed_text = await self._extract_from_embed(url)
            if embed_text:
                return ExtractedContent(
                    title=self._generate_title(embed_text),
                    full_text=embed_text[:8000],
                    url=url,
                    platform='linkedin',
                    metadata={'method': 'embed'}
                )
            
            # 2. Fallback: text from URL
            text_from_url = self._extract_text_from_url(url)
            full_text = text_from_url or "LinkedIn post content unavailable."
            
            return ExtractedContent(
                title=self._generate_title(full_text),
                full_text=full_text[:8000],
                url=url,
                platform='linkedin',
                metadata={'method': 'url-fallback'}
            )
        except Exception as e:
            return ExtractedContent(
                title=f"LinkedIn Error",
                full_text=f"Could not extract LinkedIn post: {str(e)}",
                url=url, platform='linkedin',
                metadata={'error': str(e)}
            )
    
    async def _extract_from_embed(self, url: str) -> Optional[str]:
        """Extract post content from LinkedIn's public embed endpoint."""
        # Extract activity/share ID from URL
        m = re.search(r'(?:activity|share)-(\d+)', url)
        if not m:
            return None
        post_id = m.group(1)
        
        # Try activity URN first, then share URN
        for urn_type in ('activity', 'share'):
            embed_url = f"https://www.linkedin.com/embed/feed/update/urn:li:{urn_type}:{post_id}"
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(
                        embed_url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    )
                    if resp.status_code != 200:
                        continue
                    
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    # The post text lives in the meta description on the embed page
                    meta = soup.find('meta', attrs={'name': 'description'})
                    if meta and meta.get('content') and len(meta['content']) > 30:
                        # Skip LinkedIn boilerplate text
                        content = meta['content'].strip()
                        boilerplate = ('linkedin and 3rd parties', 'cookie policy')
                        if not content.lower().startswith(boilerplate):
                            return content[:8000]
                    
                    # Fallback: paragraph selectors
                    text_parts = []
                    for p in soup.select('.feed-shared-update-v2__description, p, span.visually-hidden'):
                        t = p.get_text(strip=True)
                        if t and len(t) > 15 and t not in text_parts:
                            text_parts.append(t)
                    
                    if text_parts:
                        return '\n\n'.join(text_parts[:8])[:8000]
            except Exception:
                continue
        
        return None
    
    async def _extract_document_transcript(self, url: str) -> Optional[str]:
        """Extract full document text (PDF attachments) via LinkedIn transcript API."""
        # 1. Fetch post page HTML
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                )
                if resp.status_code != 200:
                    return None
                html = resp.text
        except Exception:
            return None

        # 2. Find the document master-manifest URL
        m = re.search(r'(https?:\\?/\\?/media\.licdn\.com/dms/document/[^"\' ]*?master-manifest[^"\' ]*)', html)
        if not m:
            return None
        manifest_url = m.group(1).replace("&amp;", "&")

        # 3. Fetch manifest -> transcriptManifestUrl -> transcript (same client)
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(manifest_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return None
                manifest = resp.json()

                tran_url = manifest.get("transcriptManifestUrl") or manifest.get("transcribedDocumentUrl")
                if not tran_url:
                    return None

                # 4. Fetch transcript -> pages[] with full text
                resp = await client.get(tran_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception:
            return None

        pages = data.get("pages") or []
        if not pages:
            return None
        text = "\n\n=== PAGE ===\n\n".join(str(p) for p in pages)
        return text[:120000]

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
    
    def _generate_title(self, text: str) -> str:
        """Generate a title from LinkedIn post text."""
        # Take first meaningful line
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in lines:
            if len(line) > 20:
                return line[:100]
        return f"LinkedIn Post ({text[:50]}...)"