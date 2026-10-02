"""
Snowflake connection utility that supports both password and private key authentication.
"""

from sqlalchemy import create_engine
from snowflake.sqlalchemy import URL
from app.config import settings
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import os


def get_snowflake_engine():
    """
    Create Snowflake engine with support for both authentication methods:
    - Password authentication
    - Private key (service account) authentication
    """
    
    connection_params = {
        'account': settings.SNOWFLAKE_ACCOUNT,
        'user': settings.SNOWFLAKE_USER,
        'database': settings.SNOWFLAKE_DATABASE,
        'schema': settings.SNOWFLAKE_SCHEMA,
        'warehouse': settings.SNOWFLAKE_WAREHOUSE,
    }
    
    if settings.SNOWFLAKE_ROLE:
        connection_params['role'] = settings.SNOWFLAKE_ROLE
    
    connect_args = {
        'session_parameters': {
            'QUOTED_IDENTIFIERS_IGNORE_CASE': 'TRUE'
        }
    }
    
    # Determine authentication method
    if settings.SNOWFLAKE_PRIVATE_KEY_PATH:
        # Use private key authentication (service account)
        print("Using private key authentication")
        
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
        
        connect_args['private_key'] = pkb
        
    elif settings.SNOWFLAKE_PASSWORD:
        # Use password authentication
        print("Using password authentication")
        connection_params['password'] = settings.SNOWFLAKE_PASSWORD
    else:
        raise ValueError(
            "Either SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH must be provided"
        )
    
    # Create the engine
    engine = create_engine(
        URL(**connection_params),
        connect_args=connect_args
    )
    
    return engine
