"""LinkedIn post content extractor.

Extracts post text and metadata from LinkedIn posts.
Uses LinkedIn's public embed endpoint (works without login).
"""
import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseExtractor, ExtractionError
from ..models import ExtractedContent
from ..config import settings


class LinkedInExtractor(BaseExtractor):
    """Extract content from LinkedIn posts."""
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(r'(?:linkedin\.com|lnkd\.in)/', url, re.IGNORECASE))
    
    @staticmethod
    def _activity_variant(url: str) -> Optional[str]:
        """پست گروهی → نسخهٔ activity.

        آدرس `urn:li:groupPost:<group>-<activity>` برای کاربر ناشناس 404 است، ولی
        همان پست با `urn:li:activity:<activity>` گاهی صفحهٔ عمومی دارد. یک تلاش
        ارزان که بعضی پست‌های گروهی را نجات می‌دهد.
        """
        m = re.search(r"urn:li:groupPost:\d+-(\d+)", url)
        if not m:
            return None
        return f"https://www.linkedin.com/feed/update/urn:li:activity:{m.group(1)}"

    async def extract(self, url: str) -> ExtractedContent:
        """Extract content from a LinkedIn post URL."""
        try:
            # 0. Expand short links (lnkd.in) FIRST — every downstream method
            # (embed regex, /posts/ URL fallback) needs the real /posts/ URL.
            fetch_url = await self._unshorten(url) if 'lnkd.in' in url else url

            # 1. NEW primary: page ld+json / meta description. LinkedIn embeds
            # the full post text as articleBody in application/ld+json even
            # behind the authwall — no login, no third-party, ~1 request.
            page_candidates = [fetch_url]
            alt = self._activity_variant(fetch_url)
            if alt:
                page_candidates.append(alt)
            for cand in page_candidates:
                page_result = await self._extract_from_page(cand)
                if page_result:
                    text, author = page_result
                    return ExtractedContent(
                        title=self._generate_title(text),
                        full_text=text[:8000],
                        url=url,
                        platform='linkedin',
                        author=author,
                        metadata={'method': 'page-ldjson', 'fetched_via': cand}
                    )

            # 2. Try document transcript next (full PDF/PPT text, no login)
            doc_result = await self._extract_document_transcript(fetch_url)
            if doc_result:
                doc_text, author = doc_result
                return ExtractedContent(
                    title=self._generate_title(doc_text),
                    full_text=doc_text,
                    url=url,
                    platform='linkedin',
                    author=author,
                    metadata={'method': 'document-transcript'}
                )

            # 3. Try the public embed endpoint (may be dead — kept as fallback)
            embed_text = await self._extract_from_embed(fetch_url)
            if embed_text:
                return ExtractedContent(
                    title=self._generate_title(embed_text),
                    full_text=embed_text[:8000],
                    url=url,
                    platform='linkedin',
                    author=self._author_from_url(fetch_url),
                    metadata={'method': 'embed'}
                )

            # 4. Bright Data Web Unlocker (اگر کلید تنظیم شده باشه)
            if settings.brightdata_key:
                bd_text = await self._extract_via_brightdata(fetch_url)
                if bd_text:
                    return ExtractedContent(
                        title=self._generate_title(bd_text),
                        full_text=bd_text[:8000],
                        url=url,
                        platform='linkedin',
                        author=self._author_from_url(fetch_url),
                        metadata={'method': 'brightdata'}
                    )

            # 5. Fallback: text from URL — if even that yields nothing, ALL
            # methods failed (deleted post, private, group post behind auth).
            # Raise — never return a placeholder as if it were content
            # (placeholders were summarized and saved to Notion as success).
            text_from_url = self._extract_text_from_url(fetch_url)
            if not text_from_url or len(text_from_url.strip()) < 30:
                raise ExtractionError(
                    "LinkedIn post content could not be extracted — the post may be "
                    "deleted, private, or a group post behind authentication.",
                    url=url,
                )
            full_text = text_from_url

            return ExtractedContent(
                title=self._generate_title(full_text),
                full_text=full_text[:8000],
                url=url,
                platform='linkedin',
                author=self._author_from_url(fetch_url),
                metadata={'method': 'url-fallback'}
            )
        except Exception as e:
            raise ExtractionError(f"Could not extract LinkedIn post: {str(e)}", url=url) from e

    async def _unshorten(self, url: str) -> str:
        """Follow lnkd.in redirects to the canonical post URL."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                )
                final = str(resp.url)
                if final.startswith('http'):
                    return final
        except Exception:
            pass
        return url

    @staticmethod
    def _looks_like_authwall(html: str) -> bool:
        """آیا این HTML صفحهٔ پست است یا دیوار ورود/ثبت‌نام لینکدین؟"""
        low = html.lower()
        markers = (
            "join linkedin now",
            "agree & join linkedin",
            "authwall",
            "guest_homepage",
            "sign up | linkedin",
            "session_redirect",
            "لطفاً وارد شوید",
        )
        return any(m in low for m in markers)

    @staticmethod
    def _is_boilerplate_text(text: str) -> bool:
        """متن‌های عمومی لینکدین که محتوای پست نیستند."""
        low = (text or "").lower()
        bad = (
            "500 million+ members",
            "manage your professional identity",
            "join linkedin",
            "build and engage with your professional network",
        )
        return any(b in low for b in bad)

    async def _extract_from_page(self, url: str) -> Optional[tuple]:
        """Fetch the post page and pull text from application/ld+json.

        LinkedIn keeps the full post body as ``articleBody`` in the JSON-LD
        block that ships with the page HTML — present even when the visible
        surface is an authwall. Fall back to og:description when absent.
        Returns (text, author) or None.
        """
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

        # 🔒 صفحهٔ لاگین/ثبت‌نام به‌جای پست: متأسفانه عنوان و description عمومی
        # لینکدین («500 million+ members …») به‌راحتی به‌عنوان «متن پست» ذخیره
        # می‌شد و سند زباله می‌ساخت. هر نشانهٔ authwall ⇒ این تلاش شکست است.
        if self._looks_like_authwall(html):
            return None

        # 1) ld+json — walk every block for articleBody / headline.
        # ⚠️ articleBody (متن کامل پست) بر headline (تیتر = برش اول متن) اولویت دارد —
        # ترتیب keys قبلی باعث می‌شد فقط ۱۳۶ کاراکترِ headline ذخیره شود.
        for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                data = json.loads(block)
            except Exception:
                continue
            found = self._find_json_text(data, ('articleBody', 'headline', 'description'), prefer='articleBody')
            if found and len(found.strip()) > 30 and not self._is_boilerplate_text(found):
                author = self._author_from_ldjson(data) or self._author_from_html(html) \
                    or self._author_from_url(url)
                return found.strip()[:8000], author

        # 2) meta og:description / description fallback (skip LinkedIn boilerplate)
        m = re.search(r'<meta property="og:description" content="([^"]*)"', html) \
            or re.search(r'<meta name="description" content="([^"]*)"', html)
        if m:
            content = m.group(1).strip()
            boilerplate = ('linkedin and 3rd parties', 'cookie policy')
            if len(content) > 30 and not content.lower().startswith(boilerplate) \
                    and not self._is_boilerplate_text(content):
                author = self._author_from_html(html) or self._author_from_url(url)
                return content[:8000], author

        return None

    @staticmethod
    def _find_json_text(obj, keys: tuple, prefer: Optional[str] = None) -> Optional[str]:
        """Depth-first search of parsed JSON-LD for text under wanted keys.

        prefer: اگر ست باشد، طولانی‌ترین مقدار آن کلید (مثل articleBody)
        بر هر کلید دیگری که زودتر پیدا شود ترجیح داده می‌شود — headline
        معمولاً فقط برشِ اولِ متن کامل است.
        """
        if prefer:
            best_pref = None

            def walk_pref(o):
                nonlocal best_pref
                if isinstance(o, dict):
                    v = o.get(prefer)
                    if isinstance(v, str) and len(v.strip()) > 30 and (best_pref is None or len(v) > len(best_pref)):
                        best_pref = v.strip()
                    for vv in o.values():
                        walk_pref(vv)
                elif isinstance(o, list):
                    for it in o:
                        walk_pref(it)

            walk_pref(obj)
            if best_pref:
                return best_pref
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in keys and isinstance(v, str) and len(v.strip()) > 30:
                    return v.strip()
                r = LinkedInExtractor._find_json_text(v, keys)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = LinkedInExtractor._find_json_text(item, keys)
                if r:
                    return r
        return None

    @staticmethod
    def _author_from_ldjson(data) -> Optional[str]:
        """Author display name from the ld+json block.

        The root SocialMediaPosting object carries ``author`` =
        {"@type":"Person","name":...}; commenters are nested under
        ``comment`` with their own Person objects. Check the root's own
        author FIRST so we never report a commenter as the author.
        """
        if isinstance(data, dict):
            auth = data.get('author')
            if isinstance(auth, dict) and isinstance(auth.get('name'), str):
                return auth['name']
            if isinstance(auth, list) and auth:
                first = auth[0]
                if isinstance(first, dict) and isinstance(first.get('name'), str):
                    return first['name']

        # Fallback: first @type=Person with a name anywhere in the tree
        author = None

        def walk(x):
            nonlocal author
            if isinstance(x, dict):
                if author is None and x.get('@type') == 'Person' and isinstance(x.get('name'), str):
                    author = x['name']
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(data)
        return author
    
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
    
    async def _extract_via_brightdata(self, url: str) -> Optional[str]:
        """Extract LinkedIn post content via Bright Data Datasets API (Web Scraper).
        
        Trigger dataset -> poll snapshot -> parse NDJSON result -> extract post text.
        Uses the LinkedIn post scraper dataset (gd_lyy3tktm25m4avu764).
        """
        if not settings.brightdata_key:
            return None
        try:
            import json
            import urllib.request
            import time

            dataset_id = "gd_lyy3tktm25m4avu764"
            trigger_url = (
                f"https://api.brightdata.com/datasets/v3/trigger"
                f"?dataset_id={dataset_id}&include_errors=true"
            )
            payload = json.dumps([{"url": url}]).encode()
            req = urllib.request.Request(
                trigger_url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {settings.brightdata_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as r:
                snap = json.loads(r.read())
            sid = snap.get("snapshot_id")
            if not sid:
                return None

            # Poll snapshot until ready
            snap_url = f"https://api.brightdata.com/datasets/v3/snapshot/{sid}"
            headers = {"Authorization": f"Bearer {settings.brightdata_key}"}
            for _ in range(30):  # up to ~5 min
                req2 = urllib.request.Request(snap_url, headers=headers)
                with urllib.request.urlopen(req2, timeout=40) as r2:
                    body = r2.read().decode("utf-8", errors="replace")
                # 202 = still running; 200 = ready
                if r2.status == 200 and body.strip():
                    break
                time.sleep(10)
            else:
                return None

            # Parse NDJSON (one JSON object per line)
            for line in body.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                # Bright Data LinkedIn scraper returns various fields; pick the post text
                text = (
                    item.get("post_text")
                    or item.get("text")
                    or item.get("description")
                    or item.get("content")
                    or (item.get("supplementary_data", {}).get("content", {}).get("com.linkedin.kfeed.sharedContextualFeedEntity") or {}).get("text")
                )
                if isinstance(text, str) and len(text) > 30:
                    return text.strip()[:8000]
                # sometimes text is nested deeper
                if isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str) and len(v) > 80 and "linkedin" not in v.lower():
                            return v.strip()[:8000]
            return None
        except Exception:
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

        author = self._author_from_html(html) or self._author_from_url(url)

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
        return text[:120000], author

    def _author_from_html(self, html: str) -> Optional[str]:
        """Parse author display name from the LinkedIn post page."""
        m = re.search(r'property="og:title"\s+content="([^"]*?) on LinkedIn', html)
        if m:
            return m.group(1).strip()
        m = re.search(r'"author":\s*"([^"]+)"', html)
        if m:
            return m.group(1).strip()
        return None

    def _author_from_url(self, url: str) -> str:
        """Fallback: extract handle from /posts/<handle>_<slug> URL."""
        m = re.search(r'/posts/([A-Za-z0-9_-]+)_', url)
        return (m.group(1) or "") if m else ""

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