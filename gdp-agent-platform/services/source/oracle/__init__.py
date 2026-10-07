"""Oracle Database as a source: connect, catalog, profile in place, extract to Parquet.

The same code runs in two places: inside Snowflake (stored procedures with an external access integration and a
Snowflake SECRET holding the password) or on the API host for an Oracle only the private network reaches (password
from an environment variable). Nothing here stores a credential.
"""
