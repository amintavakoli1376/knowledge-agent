#!/usr/bin/env python3
"""Setup script to create the Notion database for Knowledge Agent.

Usage:
    python scripts/setup_database.py
    
Requires NOTION_API_KEY in .env file or environment.
Creates a new database in your Notion workspace and prints its ID.
"""
import sys
import os
import httpx
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

NOTION_VERSION = "2025-09-03"

# Find parent page ID — user must provide this
# This can be a page ID or the ID of an existing page/database
PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "")

DATABASE_TITLE = "Knowledge Base 📚"

DATABASE_PROPERTIES = {
    "Title": {"title": {}},
    "URL": {"url": {}},
    "Platform": {
        "select": {
            "options": [
                {"name": "Arxiv", "color": "blue"},
                {"name": "Youtube", "color": "red"},
                {"name": "Twitter", "color": "blue"},
                {"name": "Linkedin", "color": "blue"},
                {"name": "Instagram", "color": "purple"},
                {"name": "Github", "color": "green"},
                {"name": "Website", "color": "gray"},
                {"name": "Telegram", "color": "blue"},
                {"name": "Medium", "color": "green"},
            ]
        }
    },
    "Category": {
        "select": {
            "options": [
                {"name": "AI/ML", "color": "purple"},
                {"name": "Technology", "color": "blue"},
                {"name": "Business", "color": "yellow"},
                {"name": "Science", "color": "green"},
                {"name": "Design", "color": "pink"},
                {"name": "Programming", "color": "orange"},
                {"name": "Social", "color": "gray"},
                {"name": "News", "color": "red"},
                {"name": "Tutorial", "color": "green"},
                {"name": "Opinion", "color": "yellow"},
                {"name": "General", "color": "gray"},
            ]
        }
    },
    "Tags": {"multi_select": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "Unread", "color": "yellow"},
                {"name": "Reading", "color": "blue"},
                {"name": "Read", "color": "green"},
                {"name": "Archive", "color": "gray"},
            ]
        }
    },
    "Priority": {
        "select": {
            "options": [
                {"name": "High", "color": "red"},
                {"name": "Medium", "color": "yellow"},
                {"name": "Low", "color": "gray"},
            ]
        }
    },
    "Summary (FA)": {"rich_text": {}},
    "Summary (EN)": {"rich_text": {}},
    "Key Points": {"rich_text": {}},
    "Author": {"rich_text": {}},
    "Date Saved": {"date": {}},
}


def main():
    api_key = os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN")
    parent_id = PARENT_PAGE_ID or input("Enter the parent page ID (UUID from Notion URL): ").strip()
    
    if not api_key:
        print("❌ NOTION_API_KEY not set!")
        print("   Set it in .env file or as environment variable.")
        sys.exit(1)
    
    if not parent_id:
        print("❌ Parent page ID is required!")
        print("   Open a page in Notion, copy the UUID from the URL.")
        sys.exit(1)
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    
    payload = {
        "parent": {"page_id": parent_id} if len(parent_id) == 36 else {"page_id": parent_id},
        "title": [{"type": "text", "text": {"content": DATABASE_TITLE}}],
        "properties": DATABASE_PROPERTIES,
    }
    
    print(f"📦 Creating database: {DATABASE_TITLE}")
    print(f"📍 Under page: {parent_id}")
    
    response = httpx.post(
        "https://api.notion.com/v1/databases",
        headers=headers,
        json=payload,
    )
    
    if response.status_code == 200:
        data = response.json()
        db_id = data["id"]
        db_url = data.get("url", f"https://notion.so/{db_id.replace('-', '')}")
        print()
        print("✅ Database created successfully!")
        print(f"   📋 Database ID: {db_id}")
        print(f"   🔗 URL: {db_url}")
        print()
        print("📝 Add this to your .env file:")
        print(f'   NOTION_DATABASE_ID={db_id}')
        print()
        print("🔗 Don't forget to share this database with your integration:")
        print("   In Notion → Share → Connect to → your integration name")
    else:
        print(f"❌ Error ({response.status_code}):")
        print(response.text[:500])
        print()
        if response.status_code == 403:
            print("💡 Make sure you shared the parent page with your integration:")
            print("   Open the parent page in Notion → Share → Connect to → your integration")
        elif response.status_code == 401:
            print("💡 Make sure your API key is correct.")


if __name__ == "__main__":
    main()
