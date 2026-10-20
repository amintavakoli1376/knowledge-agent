"""Knowledge Agent - Configuration."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    
    # Notion
    notion_api_key: str = ""
    notion_database_id: str = ""
    notion_public_base: str = "amusing-nutmeg-2ca.notion.site"
    notion_public_view_id: str = ""

    # AFFiNE (self-hosted dual-save via socket.io sync protocol)
    affine_enabled: bool = False
    affine_url: str = "http://127.0.0.1:3010"
    affine_public_url: str = "http://176.100.37.230:3010"
    affine_email: str = ""
    affine_password: str = ""
    affine_workspace_id: str = ""
    affine_client_version: str = "0.27.4"

    # Outline (self-hosted)
    outline_enabled: bool = False
    outline_url: str = "https://176.100.37.230:3012"
    outline_api_key: str = ""
    outline_collection_id: str = ""
    outline_index_doc_id: str = ""
    
    # Bright Data (Web Unlocker for LinkedIn/blocked sites)
    brightdata_key: str = ""
    
    # OpenAI / AvalAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # سقف توکن خروجی. مدل‌های reasoning اول توکن‌ها را صرف «فکر کردن» می‌کنند؛
    # اگر سقف کم باشد content خالی برمی‌گردد (finish_reason=length).
    # 0 = پارامتر فرستاده نشود (پیش‌فرض روتر).
    openai_max_tokens: int = 16000
    # توکن احراز هویت وب؛ خالی = همهٔ مسیرها بسته (fail-closed)
    web_auth_token: str = ""
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    # Local data (SQLite stores)
    data_dir: str = "/root/knowledge-agent-repo/data"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
