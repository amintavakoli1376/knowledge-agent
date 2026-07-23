"""YouTube video content extractor.

Extracts video metadata, transcript, and description from YouTube videos.
Uses youtube-transcript-api for captions and yt-dlp for metadata.
"""
import re
import json
from typing import Optional
import httpx

from .base import BaseExtractor
from ..models import ExtractedContent


class YouTubeExtractor(BaseExtractor):
    """Extract content from YouTube videos."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    
    def can_handle(self, url: str) -> bool:
        return bool(re.search(
            r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/',
            url, re.IGNORECASE
        ))
    
    async def extract(self, url: str) -> ExtractedContent:
        video_id = self._extract_video_id(url)
        if not video_id:
            return ExtractedContent(
                title="Invalid YouTube URL",
                full_text="Could not extract video ID from URL.",
                url=url, platform='youtube',
                metadata={'error': 'Invalid URL'}
            )
        
        try:
            # Get metadata via oEmbed API (no API key needed)
            metadata = await self._get_metadata(video_id)
            
            # Get transcript
            transcript_text = await self._get_transcript(video_id)
            
            # Build full text from description + transcript
            description = metadata.get('description', '')
            full_text = f"Title: {metadata.get('title', '')}\n\n"
            if description:
                full_text += f"Description:\n{description}\n\n"
            if transcript_text:
                full_text += f"Transcript:\n{transcript_text[:8000]}"
            
            # Build clean URL
            clean_url = f"https://youtube.com/watch?v={video_id}"
            
            return ExtractedContent(
                title=metadata.get('title', 'YouTube Video')[:200],
                full_text=full_text[:10000],
                url=clean_url,
                platform='youtube',
                author=metadata.get('author_name', ''),
                published_date=metadata.get('upload_date', ''),
                metadata={
                    'video_id': video_id,
                    'channel': metadata.get('author_name', ''),
                    'channel_url': metadata.get('author_url', ''),
                    'duration': metadata.get('duration', 0),
                    'thumbnail': metadata.get('thumbnail_url', ''),
                    'has_transcript': bool(transcript_text),
                }
            )
        except Exception as e:
            return ExtractedContent(
                title=f"YouTube Error: {video_id}",
                full_text=f"Failed to extract video: {str(e)}",
                url=url, platform='youtube',
                metadata={'error': str(e), 'video_id': video_id}
            )
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
            r'youtu\.be/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def _get_metadata(self, video_id: str) -> dict:
        """Get video metadata from oEmbed API (no key needed)."""
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            r = await self.client.get(oembed_url)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return {
            'title': f'YouTube Video ({video_id})',
            'author_name': 'Unknown',
            'description': '',
        }
    
    async def _get_transcript(self, video_id: str) -> Optional[str]:
        """Get video transcript/captions."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript_list = YouTubeTranscriptApi.get_transcript(
                video_id, languages=['en', 'fa', 'ar']
            )
            if transcript_list:
                return ' '.join(item['text'] for item in transcript_list)
        except:
            pass
        
        # Fallback: try yt-dlp for auto-generated captions
        try:
            import subprocess
            result = subprocess.run(
                ['yt-dlp', '--skip-download', '--write-auto-subs',
                 '--sub-lang', 'en', '--convert-subs', 'txt',
                 '--output', '/tmp/yt_%(id)s', f'https://youtube.com/watch?v={video_id}'],
                capture_output=True, text=True, timeout=30
            )
            # Try to read downloaded subtitle file
            import glob
            files = glob.glob(f'/tmp/yt_{video_id}*.txt')
            if files:
                with open(files[0], 'r') as f:
                    text = f.read()
                # Clean up
                import os
                for f in files:
                    os.remove(f)
                return text[:8000]
        except:
            pass
        
        return None
    
    async def close(self):
        await self.client.aclose()
