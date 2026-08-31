from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SaaS Implementation Agent"
    database_url: str = "sqlite+aiosqlite:///./saas_agent.db"
    migration_database_url: str = ""
    app_db_user: str = "saas_app"
    app_db_password: str = "saas_app"
    jwt_secret: str = "development-only-secret-change-me-32-bytes"
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    demo_mode: bool = False
    auto_create_schema: bool = False
    cookie_secure: bool = False
    session_hours: int = 8
    redis_url: str = "redis://localhost:6379/0"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "ChangeMe123!"
    bootstrap_admin_name: str = "平台管理员"
    legacy_tenant_name: str = "Legacy Demo"
    mail_debug: bool = True
    frontend_base_url: str = "http://localhost:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()
