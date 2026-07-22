"""Base extractor class."""
from abc import ABC, abstractmethod
from ..models import ExtractedContent


class BaseExtractor(ABC):
    """Abstract base class for content extractors."""
    
    @abstractmethod
    async def extract(self, url: str) -> ExtractedContent:
        """Extract content from the given URL."""
        pass
    
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Check if this extractor can handle the given URL."""
        pass
