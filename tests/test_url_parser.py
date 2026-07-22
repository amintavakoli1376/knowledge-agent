"""Tests for Knowledge Agent."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils.url_parser import detect_platform, is_valid_url


class TestURLParser:
    """Test URL parsing and platform detection."""
    
    def test_detect_arxiv(self):
        assert detect_platform("https://arxiv.org/abs/2401.12345") == "arxiv"
        assert detect_platform("https://arxiv.org/pdf/2401.12345.pdf") == "arxiv"
    
    def test_detect_youtube(self):
        assert detect_platform("https://youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
        assert detect_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    
    def test_detect_twitter(self):
        assert detect_platform("https://twitter.com/user/status/123") == "twitter"
        assert detect_platform("https://x.com/user/status/123") == "twitter"
    
    def test_detect_linkedin(self):
        assert detect_platform("https://linkedin.com/posts/user_123") == "linkedin"
    
    def test_detect_instagram(self):
        assert detect_platform("https://instagram.com/p/xyz123") == "instagram"
    
    def test_detect_website(self):
        assert detect_platform("https://example.com/article") == "website"
        assert detect_platform("https://some-blog.com/post") == "website"
    
    def test_invalid_urls(self):
        assert is_valid_url("https://example.com") == True
        assert is_valid_url("http://example.com") == True
        assert is_valid_url("not-a-url") == False
        assert is_valid_url("") == False
    
    def test_extract_domain(self):
        from src.utils.url_parser import extract_domain
        assert extract_domain("https://www.example.com/page") == "example.com"
        assert extract_domain("https://arxiv.org/abs/123") == "arxiv.org"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
