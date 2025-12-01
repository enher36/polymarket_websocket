"""Configuration management using pydantic-settings for lazy loading."""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings are lazily loaded when first accessed via get_settings().
    """

    model_config = SettingsConfigDict(
        env_prefix="POLYMARKET_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # API Configuration
    api_url: str = Field(
        default="https://gamma-api.polymarket.com",
        description="Polymarket REST API base URL",
    )
    ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        description="Polymarket WebSocket URL",
    )

    # Database
    db_path: str = Field(
        default="polymarket.db",
        description="SQLite database file path",
    )

    # HTTP Settings
    http_timeout: float = Field(
        default=10.0,
        description="HTTP request timeout in seconds",
    )
    http_rps: float = Field(
        default=2.0,
        description="HTTP requests per second rate limit",
    )

    # WebSocket Settings
    ws_heartbeat_sec: float = Field(
        default=15.0,
        description="WebSocket heartbeat interval in seconds",
    )
    ws_reconnect_sec: float = Field(
        default=5.0,
        description="WebSocket reconnect delay in seconds",
    )

    # Forwarding Server Settings
    forward_enabled: bool = Field(
        default=False,
        description="Enable local WebSocket forwarding server",
    )
    forward_host: str = Field(
        default="127.0.0.1",
        description="Host for local forwarding WebSocket server",
    )
    forward_port: int = Field(
        default=8765,
        description="Port for local forwarding WebSocket server",
    )

    # Web Monitoring Server Settings
    web_enabled: bool = Field(
        default=True,
        description="Enable HTTP monitoring server for dashboard",
    )
    web_host: str = Field(
        default="127.0.0.1",
        description="Host for HTTP monitoring server",
    )
    web_port: int = Field(
        default=8080,
        description="Port for HTTP monitoring server",
    )

    # Scanner Settings
    scan_interval_sec: int = Field(
        default=300,
        description="Market scanner interval in seconds",
    )
    category: Optional[str] = Field(
        default=None,
        description="Category filter for market scanning",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Uses lru_cache to ensure settings are only loaded once.
    """
    return Settings()
