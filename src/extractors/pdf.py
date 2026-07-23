"""PDF file content extractor.

Accepts PDF files and extracts text content using PyMuPDF.
Supports both file uploads and URLs.
"""
import os
import httpx
import tempfile
from typing import Optional

from .base import BaseExtractor
from ..models import ExtractedContent


class PDFExtractor(BaseExtractor):
    """Extract text content from PDF files."""
    
    def can_handle(self, url: str) -> bool:
        url_lower = url.lower().strip()
        return url_lower.endswith('.pdf') or '/pdf/' in url_lower
    
    async def extract(self, url: str) -> ExtractedContent:
        """Download PDF and extract text."""
        temp_path = None
        try:
            # Download the PDF
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                content = response.content
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(content)
                temp_path = tmp.name
            
            # Extract text using PyMuPDF
            import fitz
            doc = fitz.open(temp_path)
            
            full_text = ""
            title = ""
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text().strip()
                
                # First page often has title
                if page_num == 0 and text:
                    lines = text.split('\n')
                    title = lines[0][:200] if lines else "PDF Document"
                
                full_text += text + "\n\n"
            
            doc.close()
            
            # Clean up
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            
            if not full_text.strip():
                return ExtractedContent(
                    title="PDF (No extractable text)",
                    full_text="This PDF appears to be scanned or image-based. No text could be extracted.",
                    url=url, platform='pdf',
                    metadata={'note': 'Scanned PDF - OCR needed'}
                )
            
            # Generate title from filename if not found
            if not title or len(title) < 5:
                title = url.split('/')[-1].replace('.pdf', '').replace('-', ' ').replace('_', ' ')[:100]
            
            return ExtractedContent(
                title=title,
                full_text=full_text[:10000],
                url=url,
                platform='pdf',
                metadata={
                    'pages': len(doc) if 'doc' in dir() else 0,
                    'size_kb': len(content) // 1024,
                }
            )
            
        except Exception as e:
            # Clean up temp file if exists
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            
            return ExtractedContent(
                title="PDF Error",
                full_text=f"Could not extract PDF: {str(e)}",
                url=url, platform='pdf',
                metadata={'error': str(e)}
            )
