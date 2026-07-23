"""Data models for Knowledge Agent."""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ExtractedContent(BaseModel):
    """Normalized content from any platform."""
    title: str = "Untitled"
    full_text: str = ""
    url: str = ""
    platform: str = "website"
    author: Optional[str] = None
    published_date: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class AIAnalysis(BaseModel):
    """AI-generated analysis of content."""
    summary_fa: str = ""
    summary_en: str = ""
    key_points: List[str] = Field(default_factory=list)
    category: str = "General"
    tags: List[str] = Field(default_factory=list)
    priority: str = "Medium"


class SaveRequest(BaseModel):
    """API save request."""
    url: str
    platform: Optional[str] = None  # auto-detect if not provided
    note: Optional[str] = None


class SaveResponse(BaseModel):
    """Response after saving to Notion."""
    success: bool
    notion_url: Optional[str] = None
    title: str = ""
    platform: str = ""
    category: str = ""
    error: Optional[str] = None


class SetupRequest(BaseModel):
    """Request to auto-create Notion database."""
    parent_page_id: str


class SetupResponse(BaseModel):
    """Response after database creation."""
    success: bool
    database_id: Optional[str] = None
    database_url: Optional[str] = None
    error: Optional[str] = None
