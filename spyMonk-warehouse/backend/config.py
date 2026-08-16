"""
Configuration management for spyMonk-warehouse backend
"""
import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


def parse_csv(value: str) -> List[str]:
    """Parse comma-separated environment values while ignoring empty entries."""
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    # API Security
    API_SECRET_KEY: str = "change-this-secret-key-in-production"
    API_KEY_HEADER: str = "X-API-Key"
    ALLOWED_API_KEYS_RAW: str = Field(default="", alias="ALLOWED_API_KEYS")

    # CORS Configuration
    CORS_ORIGINS_RAW: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    # spyMonk-DB Connection
    SPYMONK_DB_NODES_RAW: str = Field(default="localhost:5000", alias="SPYMONK_DB_NODES")
    SPYMONK_DB_AUTH_TOKEN: str = os.getenv("SPYMONK_DB_AUTH_TOKEN", "")
    USE_DISTRIBUTED_MODE: bool = os.getenv("USE_DISTRIBUTED_MODE", "false").lower() == "true"
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/spymonk_warehouse_db")

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    UPLOAD_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("UPLOAD_RATE_LIMIT_PER_MINUTE", "10"))

    # File Upload Settings
    UPLOAD_MAX_SIZE_MB: int = int(os.getenv("UPLOAD_MAX_SIZE_MB", "100"))
    ALLOWED_FILE_EXTENSIONS: List[str] = [".csv", ".json", ".xlsx"]

    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Security Headers
    ENABLE_HTTPS_REDIRECT: bool = os.getenv("ENABLE_HTTPS_REDIRECT", "false").lower() == "true"

    # AI Assistant
    AI_API_KEY: str = os.getenv("AI_API_KEY", "")
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_SECONDS: int = int(os.getenv("AI_TIMEOUT_SECONDS", "12"))

    # Snowflake-style storage / caching
    PARTITION_ROWS: int = int(os.getenv("PARTITION_ROWS", "5000"))
    RESULT_CACHE_MAX_ENTRIES: int = int(os.getenv("RESULT_CACHE_MAX_ENTRIES", "256"))
    RESULT_CACHE_TTL_SECONDS: int = int(os.getenv("RESULT_CACHE_TTL_SECONDS", "1800"))
    PRUNING_ENABLED: bool = os.getenv("PRUNING_ENABLED", "true").lower() == "true"

    @property
    def ALLOWED_API_KEYS(self) -> List[str]:
        return parse_csv(self.ALLOWED_API_KEYS_RAW)

    @property
    def CORS_ORIGINS(self) -> List[str]:
        return parse_csv(self.CORS_ORIGINS_RAW)

    @property
    def SPYMONK_DB_NODES(self) -> List[str]:
        return parse_csv(self.SPYMONK_DB_NODES_RAW)

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
