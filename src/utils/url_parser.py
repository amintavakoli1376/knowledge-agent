"""URL parsing and platform detection utilities."""
import re
from typing import Optional


PLATFORM_PATTERNS = {
    'instagram': r'(?:https?://)?(?:www\.)?instagram\.com/',
    'linkedin': r'(?:https?://)?(?:www\.)?linkedin\.com/',
    'twitter': r'(?:https?://)?(?:www\.)?(?:twitter|x)\.com/',
    'youtube': r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/',
    'arxiv': r'(?:https?://)?(?:www\.)?arxiv\.org/',
    'telegram': r'(?:https?://)?(?:t\.me|telegram\.me)/',
    'medium': r'(?:https?://)?(?:www\.)?medium\.com/',
    'github': r'(?:https?://)?(?:www\.)?github\.com/',
    'pdf': r'\.pdf(\?|#|$)',
}


def detect_platform(url: str) -> str:
    """Detect platform from URL.
    
    Returns platform name string or 'website' as fallback.
    """
    url = url.strip()
    for platform, pattern in PLATFORM_PATTERNS.items():
        if re.match(pattern, url, re.IGNORECASE):
            return platform
    return 'website'


def extract_domain(url: str) -> str:
    """Extract domain name from URL for display."""
    match = re.search(r'https?://(?:www\.)?([^/]+)', url)
    return match.group(1) if match else url


def is_valid_url(url: str) -> bool:
    """Basic URL validation."""
    return bool(re.match(r'https?://', url.strip()))
