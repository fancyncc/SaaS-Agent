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
    execution_mode: str = "inline"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_from: str = "implementation@example.test"
    storage_backend: str = "database"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "delivery"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    model_mode: str = "deterministic"
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = ""
    model_timeout: int = 30
    embedding_model: str = ""
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
