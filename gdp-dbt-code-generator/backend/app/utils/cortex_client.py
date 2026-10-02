"""
Snowflake Cortex client for AI completions.

This module provides a helper function to call Snowflake Cortex LLM functions
as a replacement for Azure OpenAI.
"""

from snowflake.snowpark import Session
from app.config import settings
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import logging

logger = logging.getLogger(__name__)


def get_snowpark_session() -> Session:
    """Create a Snowpark session for Cortex calls with support for both password and private key authentication."""
    connection_params = {
        "account": settings.SNOWFLAKE_ACCOUNT,
        "user": settings.SNOWFLAKE_USER,
        "database": settings.SNOWFLAKE_DATABASE,
        "schema": settings.SNOWFLAKE_SCHEMA,
        "warehouse": settings.SNOWFLAKE_WAREHOUSE,
    }
    
    if settings.SNOWFLAKE_ROLE:
        connection_params["role"] = settings.SNOWFLAKE_ROLE
    
    # Add timeout to prevent infinite hangs (5 minutes)
    connection_params["session_parameters"] = {
        "STATEMENT_TIMEOUT_IN_SECONDS": 300
    }
    
    # Determine authentication method
    if settings.SNOWFLAKE_PRIVATE_KEY_PATH:
        # Use private key authentication (service account)
        logger.info("Using private key authentication for Snowpark session")
        
        try:
            # Read the private key file
            with open(settings.SNOWFLAKE_PRIVATE_KEY_PATH, 'rb') as key_file:
                p_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=settings.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE.encode() if settings.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE else None,
                    backend=default_backend()
                )
            
            # Serialize the private key to DER format
            pkb = p_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            connection_params["private_key"] = pkb
            
        except Exception as e:
            logger.error(f"Error loading private key: {str(e)}")
            raise ValueError(f"Failed to load private key from {settings.SNOWFLAKE_PRIVATE_KEY_PATH}: {str(e)}")
            
    elif settings.SNOWFLAKE_PASSWORD:
        # Use password authentication
        logger.info("Using password authentication for Snowpark session")
        connection_params["password"] = settings.SNOWFLAKE_PASSWORD
    else:
        raise ValueError(
            "Either SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH must be provided"
        )
    
    return Session.builder.configs(connection_params).create()


def cortex_complete(prompt: str, model: str = settings.CORTEX_MODEL, max_tokens: int = 4096) -> str:
    """
    Call Snowflake Cortex to generate text completion.
    
    Args:
        prompt: The input prompt for the LLM
        model: The Cortex model to use (default from settings)
        max_tokens: Maximum tokens in response (note: Cortex may have different limits)
    """
    session = get_snowpark_session()
    try:
        # Escape single quotes in prompt for SQL
        escaped_prompt = prompt.replace("'", "''")

        # Switch to SNOWFLAKE database context to use AI_COMPLETE
        # This is necessary because the session connects to DBT_GENERATOR by default
        session.sql("USE DATABASE SNOWFLAKE").collect()
        
        # Use AI_COMPLETE with named parameters (model =>, prompt =>)
        # This matches the syntax that works in Snowflake worksheets.
        sql = f"""
            SELECT AI_COMPLETE(
                model => '{model}',
                prompt => '{escaped_prompt}'
            ) AS response
        """
        
        logger.info(f"Calling Cortex model: {model} with prompt length: {len(prompt)}")
        logger.debug(f"Generated SQL: {sql}")
        result = session.sql(sql).collect()
        
        # Switch back to original database
        session.sql(f"USE DATABASE {settings.SNOWFLAKE_DATABASE}").collect()
        
        if result and len(result) > 0:
            response = result[0]["RESPONSE"]
            logger.debug(f"Raw response type: {type(response)}, value: {response}")
            
            # Handle different response types
            if response:
                # If it's a string, return directly
                if isinstance(response, str):
                    return response
                # Otherwise convert to string
                return str(response)
            else:
                logger.warning("Cortex returned empty response")
                return ""
        else:
            raise Exception("No response row returned from Cortex query")
            
    except Exception as e:
        logger.error(f"Error calling Cortex: {str(e)}")
        raise
    finally:
        session.close()
