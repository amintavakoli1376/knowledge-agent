"""Knowledge Agent - Configuration."""
from pydantic_settings import BaseSettings
from pydantic import model_validator
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
    
    # Odoo 19 (JSON-RPC 2.0 mirror) — Epic 2
    odoo_enabled: bool = False
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_api_key: str = ""
    odoo_uid: int = 0
    # آستانه‌ی حجم body (کاراکتر). بالاتر از این → بدنه کامل به ir.attachment می‌رود.
    odoo_body_size_limit: int = 50000

    # Postgres migration target (Epic 3) — schema `ka` on the Odoo PG cluster
    pg_enabled: bool = False
    pg_host: str = "127.0.0.1"
    pg_port: int = 5544
    pg_user: str = "odoo"
    pg_password: str = ""
    pg_db: str = "postgres"
    pg_schema: str = "ka"

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
    
    # Arvan S3 Backups
    arvan_endpoint: str = ""
    arvan_access_key: str = ""
    arvan_secret_key: str = ""
    arvan_region: str = ""
    arvan_bucket: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @model_validator(mode="after")
    def _check_odoo_required(self):
        if self.odoo_enabled:
            missing = [
                name
                for name, val in (
                    ("odoo_url", self.odoo_url),
                    ("odoo_db", self.odoo_db),
                    ("odoo_api_key", self.odoo_api_key),
                    ("odoo_uid", self.odoo_uid),
                )
                if not val
            ]
            if missing:
                raise ValueError(
                    "ODOO_ENABLED=true requires: " + ", ".join(missing)
                )
        return self


settings = Settings()
