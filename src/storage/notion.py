"""Notion storage client using httpx (async)."""
import httpx
from datetime import datetime
from typing import Optional

from ..config import settings
from ..models import ExtractedContent, AIAnalysis


NOTION_VERSION = "2025-09-03"


class NotionStorage:
    """Save content to Notion database using REST API directly."""
    
    def __init__(self):
        self.api_key = settings.notion_api_key
        self.database_id = settings.notion_database_id
        self.client = None
    
    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
    
    async def save(self, content: ExtractedContent, analysis: AIAnalysis) -> Optional[str]:
        """Save content as a new page in Notion database.
        
        Returns the page URL if successful, None otherwise.
        """
        if not self.api_key:
            return None
        
        if not self.database_id:
            raise Exception("NOTION_DATABASE_ID not configured")
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Build properties payload - only include non-empty, valid fields
            properties = {}
            
            # Title
            properties["Title"] = {
                "title": [{"text": {"content": (content.title[:100] or "Untitled")}}]
            }
            
            # URL (actual link)
            if content.url:
                properties["URL"] = {"url": content.url}
            
            # Tags
            if analysis.tags:
                properties["Tags"] = {
                    "multi_select": [{"name": t} for t in analysis.tags[:10]]
                }
            
            # Status
            properties["Status"] = {"select": {"name": "Unread"}}
            
            # Summaries
            if analysis.summary_fa:
                properties["Summary (FA)"] = {
                    "rich_text": [{"text": {"content": analysis.summary_fa[:2000]}}]
                }
            
            if analysis.summary_en:
                properties["Summary (EN)"] = {
                    "rich_text": [{"text": {"content": analysis.summary_en[:2000]}}]
                }
            
            # Key Points
            if analysis.key_points:
                properties["Key Points"] = {
                    "rich_text": [{
                        "text": {"content": "\n".join(f"• {kp}" for kp in analysis.key_points[:10])}
                    }]
                }
            
            # Author
            if content.author:
                properties["Author"] = {
                    "rich_text": [{"text": {"content": content.author[:200]}}]
                }
            
            # Platform
            if content.platform:
                properties["Platform"] = {
                    "select": {"name": content.platform.capitalize()}
                }
            
            # Category
            if analysis.category:
                properties["Category"] = {
                    "select": {"name": analysis.category}
                }
            
            # Date Saved
            properties["Date Saved"] = {
                "date": {"start": datetime.now().strftime("%Y-%m-%d")}
            }
            
            payload = {
                "parent": {"database_id": self.database_id},
                "properties": properties,
            }
            
            response = await client.post(
                "https://api.notion.com/v1/pages",
                headers=self._get_headers(),
                json=payload,
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("url")
            else:
                error_body = response.text[:500]
                raise Exception(f"Notion API error ({response.status_code}): {error_body}")
