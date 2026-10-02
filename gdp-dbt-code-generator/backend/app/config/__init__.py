"""
This package initializes the configuration module for the application.
It provides access to configuration settings used throughout the backend.

Typically, the settings are loaded via environment variables or default values.
"""
from .settings import Settings

# Create a settings instance to be imported and used throughout the app
settings = Settings()
