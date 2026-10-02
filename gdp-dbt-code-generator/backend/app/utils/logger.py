"""
Logging utility for the FastAPI application.

This module sets up application-wide logging with customizable log levels
and formats for consistent and centralized logging.
"""

import logging
from app.config import settings


def setup_logger():
    """
    Configures the logging for the application.

    The log level and format are controlled by the application's settings.
    """
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_level = settings.LOG_LEVEL.upper()

    # Configure the root logger
    logging.basicConfig(level=log_level, format=log_format)

    # Example of configuring a specific logger
    app_logger = logging.getLogger("app")
    app_logger.setLevel(log_level)

    # Example log for verification
    app_logger.info("Logger initialized with level: %s", log_level)
