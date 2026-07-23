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
    
    async def create_database(self, parent_page_id: str) -> tuple:
        """Create the Knowledge Base database automatically.
        
        Returns: (database_id, database_url)
        """
        if not self.api_key:
            raise Exception("NOTION_API_KEY not configured")
        
        DATABASE_TITLE = "Knowledge Base 📚"
        DATABASE_PROPERTIES = {
            "Title": {"title": {}},
            "URL": {"url": {}},
            "Platform": {"select": {"options": [
                {"name": "Arxiv", "color": "blue"},
                {"name": "Youtube", "color": "red"},
                {"name": "Twitter", "color": "gray"},
                {"name": "Linkedin", "color": "blue"},
                {"name": "Instagram", "color": "purple"},
                {"name": "Github", "color": "green"},
                {"name": "Website", "color": "gray"},
                {"name": "Telegram", "color": "blue"},
                {"name": "Medium", "color": "green"},
            ]}},
            "Category": {"select": {"options": [
                {"name": "AI/ML", "color": "purple"},
                {"name": "Technology", "color": "blue"},
                {"name": "Science", "color": "green"},
                {"name": "Business", "color": "yellow"},
                {"name": "Social", "color": "gray"},
                {"name": "Tutorial", "color": "orange"},
                {"name": "General", "color": "gray"},
            ]}},
            "Tags": {"multi_select": {}},
            "Status": {"select": {"options": [
                {"name": "Unread", "color": "yellow"},
                {"name": "Reading", "color": "blue"},
                {"name": "Read", "color": "green"},
                {"name": "Archive", "color": "gray"},
            ]}},
            "Summary (FA)": {"rich_text": {}},
            "Summary (EN)": {"rich_text": {}},
            "Key Points": {"rich_text": {}},
            "Author": {"rich_text": {}},
            "Date Saved": {"date": {}},
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.notion.com/v1/databases",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json={
                    "parent": {"page_id": parent_page_id},
                    "title": [{"type": "text", "text": {"content": DATABASE_TITLE}}],
                    "properties": DATABASE_PROPERTIES,
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                db_id = data["id"]
                db_url = data.get("url", "")
                return db_id, db_url
            else:
                error_body = response.text[:500]
                raise Exception(f"Notion DB creation failed ({response.status_code}): {error_body}")
