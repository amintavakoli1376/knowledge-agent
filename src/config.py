"""Knowledge Agent - Configuration."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = ""
    
    # Notion
    notion_api_key: str = ""
    notion_database_id: str = ""
    
    # OpenAI
    openai_api_key: str = ""
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
