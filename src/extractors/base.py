"""Base extractor class."""
from abc import ABC, abstractmethod
from ..models import ExtractedContent


class ExtractionError(Exception):
    """Raised when content extraction fails.

    Extractors must raise this instead of returning an ExtractedContent with
    an error box as full_text — those garbage payloads were being saved to
    Notion and posted to the user as if they were real content.
    """

    def __init__(self, message: str, url: str = ""):
        self.url = url
        super().__init__(message)


class BaseExtractor(ABC):
    """Abstract base class for content extractors."""
    
    @abstractmethod
    async def extract(self, url: str) -> ExtractedContent:
        """Extract content from the given URL. Raise ExtractionError on failure."""
        pass
    
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Check if this extractor can handle the given URL."""
        pass
