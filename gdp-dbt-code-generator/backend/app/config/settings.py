"""
Settings module for the application.

This module defines configuration settings using Pydantic's `BaseSettings`.
Settings are loaded from environment variables or default values, with support
for an `.env` file for local development.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # General application settings
    APP_NAME: str = Field("DBT Code Generator", env="APP_NAME")
    ENVIRONMENT: str = Field("development", env="ENVIRONMENT")  # Options: development, production
    DEBUG: bool = Field(True, env="DEBUG")

    # Snowflake settings
    SNOWFLAKE_ACCOUNT: str = Field(..., env="SNOWFLAKE_ACCOUNT")
    SNOWFLAKE_USER: str = Field(..., env="SNOWFLAKE_USER")
    
    # Authentication: Use either password OR private key
    SNOWFLAKE_PASSWORD: Optional[str] = Field(None, env="SNOWFLAKE_PASSWORD")
    SNOWFLAKE_PRIVATE_KEY_PATH: Optional[str] = Field(None, env="SNOWFLAKE_PRIVATE_KEY_PATH")
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE: Optional[str] = Field(None, env="SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    
    SNOWFLAKE_DATABASE: str = Field(..., env="SNOWFLAKE_DATABASE")
    SNOWFLAKE_SCHEMA: str = Field(..., env="SNOWFLAKE_SCHEMA")
    SNOWFLAKE_WAREHOUSE: str = Field(..., env="SNOWFLAKE_WAREHOUSE")
    SNOWFLAKE_ROLE: str = Field(None, env="SNOWFLAKE_ROLE")

    # Cortex model setting (Snowflake Cortex model name)
    # Default to OpenAI GPT-5 model via Cortex
    CORTEX_MODEL: str = Field("openai-gpt-5", env="CORTEX_MODEL")

    # Logging settings
    LOG_LEVEL: str = Field("info", env="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Create a settings instance to be used across the application
settings = Settings()