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

            # Analysis Status (مقاوم‌سازی 429): فقط وقتی pending است ست می‌شود —
            # صفحات موفق legacy این پراپرتی را ندارند (سازگار با دیتابیس فعلی)
            analysis_status = getattr(analysis, "analysis_status", "") or ""
            analysis_error = getattr(analysis, "analysis_error", "") or ""
            include_flag = bool(analysis_status or analysis_error)
            if analysis_status:
                properties["Analysis Status"] = {"select": {"name": analysis_status}}

            if analysis_error:
                properties["Analysis Error"] = {
                    "rich_text": [{"text": {"content": analysis_error[:2000]}}]
                }
            
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

            if response.status_code != 200 and include_flag:
                # احتمالاً پراپرتی‌های Analysis Status/Error هنوز در schema
                # دیتابیس نیستند (Notion → 400 validation_error).
                # تلاش مجدد بدون flag — ثبت flag حیاتی نیست، ذخیرهٔ سند هست.
                retry = await self._ensure_flag_properties()
                if retry:
                    response = await client.post(
                        "https://api.notion.com/v1/pages",
                        headers=self._get_headers(),
                        json=payload,
                    )
                else:
                    properties.pop("Analysis Status", None)
                    properties.pop("Analysis Error", None)
                    payload["properties"] = properties
                    response = await client.post(
                        "https://api.notion.com/v1/pages",
                        headers=self._get_headers(),
                        json=payload,
                    )

            if response.status_code == 200:
                data = response.json()
                page_id = data.get("id", "")
                # ساخت لینک پابلیش شده (بدون نیاز به لاگین)
                published_url = self._build_public_url(page_id)
                return published_url
            else:
                error_body = response.text[:500]
                raise Exception(f"Notion API error ({response.status_code}): {error_body}")

    async def _ensure_flag_properties(self) -> bool:
        """افزودن ستون‌های Analysis Status/Error به دیتابیس Notion (یک‌بار).

        Returns True اگر عملیات schema موفق بود (چه ستون‌ها اضافه شدند چه
        از قبل موجود بودند).
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.patch(
                    f"https://api.notion.com/v1/databases/{self.database_id}",
                    headers=self._get_headers(),
                    json={
                        "properties": {
                            "Analysis Status": {"select": {}},
                            "Analysis Error": {"rich_text": {}},
                        }
                    },
                )
                return response.status_code == 200
        except Exception:
            return False
    
    def _build_public_url(self, page_id: str) -> str:
        """ساخت لینک پابلیش‌شده دیتابیس برای دسترسی بدون لاگین."""
        base = settings.notion_public_base
        view_id = settings.notion_public_view_id
        if not page_id:
            return ""
        pid = page_id.replace("-", "")
        url = f"https://{base}/{pid}"
        if view_id:
            url += f"?v={view_id}"
        return url

    # ------------------------------------------------------------------
    # مقاوم‌سازی 429-quota (t_c270e218) — patch خلاصه پس از ریکاوری
    # ------------------------------------------------------------------

    async def find_page_by_url(self, url: str) -> Optional[str]:
        """پیدا کردن page_id سند Notion بر اساس URL ذخیره‌شده (best-effort).

        Returns page_id یا None (وقتی سند نبود/خطا — هرگز raise نمی‌کند).
        """
        if not self.api_key or not self.database_id or not url:
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"https://api.notion.com/v1/databases/{self.database_id}/query",
                    headers=self._get_headers(),
                    json={"filter": {"property": "URL", "url": {"equals": url}}},
                )
                if response.status_code != 200:
                    return None
                results = response.json().get("results", [])
                return results[0]["id"] if results else None
        except Exception:
            return None

    async def patch_summary(self, page_id: str, analysis: AIAnalysis) -> bool:
        """پرکردن خلاصه‌های یک سند Notion موجود (پس از ریکاوری موفق).

        Status تحلیل پاک می‌شود (None) و خلاصه‌ها/نکات/دسته/برچسب‌ها patch می‌شوند.
        Returns True فقط در موفقیت کامل (HTTP 200).
        """
        if not self.api_key or not page_id:
            return False

        properties = {}
        if analysis.summary_fa:
            properties["Summary (FA)"] = {
                "rich_text": [{"text": {"content": analysis.summary_fa[:2000]}}]
            }
        if analysis.summary_en:
            properties["Summary (EN)"] = {
                "rich_text": [{"text": {"content": analysis.summary_en[:2000]}}]
            }
        if analysis.key_points:
            properties["Key Points"] = {
                "rich_text": [{
                    "text": {"content": "\n".join(f"• {kp}" for kp in analysis.key_points[:10])}
                }]
            }
        if analysis.category:
            properties["Category"] = {"select": {"name": analysis.category}}
        if analysis.tags:
            properties["Tags"] = {
                "multi_select": [{"name": t} for t in analysis.tags[:10]]
            }
        # پاک‌کردن flag pending — select خالی یعنی بدون مقدار
        properties["Analysis Status"] = {"select": None}
        properties["Analysis Error"] = {"rich_text": []}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=self._get_headers(),
                    json={"properties": properties},
                )
                if response.status_code != 200:
                    # شاید ستون‌های flag در schema نیستند → بدون آن‌ها دوباره
                    retry = await self._ensure_flag_properties()
                    if not retry:
                        properties.pop("Analysis Status", None)
                        properties.pop("Analysis Error", None)
                        response = await client.patch(
                            f"https://api.notion.com/v1/pages/{page_id}",
                            headers=self._get_headers(),
                            json={"properties": properties},
                        )
                return response.status_code == 200
        except Exception:
            return False
    
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
