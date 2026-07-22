"""ArXiv paper extractor."""
import arxiv
from typing import Optional
import re

from .base import BaseExtractor
from ..models import ExtractedContent


class ArxivExtractor(BaseExtractor):
    """Extract content from ArXiv papers."""
    
    def can_handle(self, url: str) -> bool:
        return 'arxiv.org' in url.lower()
    
    async def extract(self, url: str) -> ExtractedContent:
        try:
            # Extract paper ID from URL
            paper_id = self._extract_id(url)
            if not paper_id:
                return ExtractedContent(
                    title="Invalid ArXiv URL",
                    full_text="Could not extract paper ID from URL.",
                    url=url,
                    platform='arxiv',
                    metadata={'error': 'Invalid URL'}
                )
            
            # Search for the paper
            search = arxiv.Search(id_list=[paper_id], max_results=1)
            client = arxiv.Client()
            results = list(client.results(search))
            
            if not results:
                return ExtractedContent(
                    title="Paper not found",
                    full_text=f"No paper found with ID: {paper_id}",
                    url=url,
                    platform='arxiv',
                    metadata={'error': 'Not found'}
                )
            
            paper = results[0]
            
            return ExtractedContent(
                title=paper.title,
                full_text=paper.summary,
                url=paper.entry_id or url,
                platform='arxiv',
                author=', '.join(a.name for a in paper.authors[:5]),
                published_date=str(paper.published.date()) if paper.published else None,
                metadata={
                    'arxiv_id': paper_id,
                    'categories': paper.categories,
                    'authors_full': [a.name for a in paper.authors],
                    'pdf_url': paper.pdf_url,
                    'comment': paper.comment or '',
                }
            )
        except Exception as e:
            return ExtractedContent(
                title=f"ArXiv Error: {url}",
                full_text=f"Failed to extract paper: {str(e)}",
                url=url,
                platform='arxiv',
                metadata={'error': str(e)}
            )
    
    def _extract_id(self, url: str) -> Optional[str]:
        """Extract ArXiv paper ID from various URL formats."""
        patterns = [
            r'arxiv\.org/abs/(\d+\.\d+)',
            r'arxiv\.org/pdf/(\d+\.\d+)',
            r'arxiv\.org/abs/(\d+\.\d+v\d+)',
            r'arxiv\.org/pdf/(\d+\.\d+v\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
