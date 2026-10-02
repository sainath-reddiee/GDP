"""Shared Snowflake connection utility.

Provides a single get_snowflake_connection() function used by ALL modules.
Supports both Streamlit in Snowflake (SiS) native sessions and local
development with environment variables.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Running in SiS or dotenv not installed


def get_snowflake_connection(**kwargs):
    """Create a Snowflake connection using native session (SiS) or explicit params/env.

    Priority:
        1. Native Snowflake session (Streamlit in Snowflake) - ALWAYS tried first
        2. Explicit kwargs passed by caller
        3. Environment variables (local development)

    Args:
        **kwargs: Optional connection parameters (account, user, password,
                  role, warehouse, database, schema).

    Returns:
        A Snowflake connection object.
    """
    # 1. ALWAYS try native Snowflake session first (Streamlit in Snowflake)
    # In SiS there is no password, so this must take priority even when kwargs are passed.
    try:
        from snowflake.snowpark.context import get_active_session
        session = get_active_session()
        return session.connection
    except (ImportError, Exception):
        pass

    # 2. Use kwargs or fallback to environment variables (local development)
    import snowflake.connector

    account = (kwargs.get("account") or os.getenv("SNOWFLAKE_ACCOUNT") or "").strip()
    if account.endswith(".snowflakecomputing.com"):
        account = account.replace(".snowflakecomputing.com", "")

    return snowflake.connector.connect(
        account=account,
        user=(kwargs.get("user") or os.getenv("SNOWFLAKE_USER") or "").strip() or None,
        password=(kwargs.get("password") or os.getenv("SNOWFLAKE_PASSWORD") or "").strip() or None,
        role=(kwargs.get("role") or os.getenv("SNOWFLAKE_ROLE") or "").strip() or None,
        warehouse=(kwargs.get("warehouse") or os.getenv("SNOWFLAKE_WAREHOUSE") or "").strip() or None,
        database=(kwargs.get("database") or os.getenv("SNOWFLAKE_DATABASE") or "").strip() or None,
        schema=(kwargs.get("schema") or os.getenv("SNOWFLAKE_SCHEMA") or "").strip() or None,
    )


def get_llm_model():
    """Get the configured LLM model name."""
    return os.getenv("SNOWFLAKE_LLM_MODEL", "llama3-70b")


def get_embed_model():
    """Get the configured embedding model name."""
    return os.getenv("SNOWFLAKE_EMBED_MODEL", "e5-base-v2")


def is_running_in_sis():
    """Check if we are running inside Streamlit in Snowflake."""
    try:
        from snowflake.snowpark.context import get_active_session
        get_active_session()
        return True
    except (ImportError, Exception):
        return False