import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the backend directory BEFORE importing settings
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from sqlalchemy import create_engine, text
from snowflake.sqlalchemy import URL
from app.config.settings import settings

# Create Snowflake engine
engine = create_engine(
    URL(
        account=settings.SNOWFLAKE_ACCOUNT,
        user=settings.SNOWFLAKE_USER,
        password=settings.SNOWFLAKE_PASSWORD,
        database=settings.SNOWFLAKE_DATABASE,
        schema=settings.SNOWFLAKE_SCHEMA,
        warehouse=settings.SNOWFLAKE_WAREHOUSE,
        role=settings.SNOWFLAKE_ROLE,
    )
)

def run_migration():
    columns_to_add = [
        ("CLEANING_LOGIC", "TEXT DEFAULT ''"),
        ("MERGE_STRATEGY", "VARCHAR(100) DEFAULT 'UNION'"),
        ("MACROS", "TEXT DEFAULT ''")
    ]
    
    with engine.connect() as conn:
        for col_name, col_def in columns_to_add:
            try:
                print(f"Adding column {col_name} to MAPPING_ROWS...")
                conn.execute(text(f"ALTER TABLE MAPPING_ROWS ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"  Successfully added {col_name}")
            except Exception as e:
                if "already exists" in str(e).lower():
                    print(f"  Column {col_name} already exists. Skipping.")
                else:
                    print(f"  Error adding column {col_name}: {e}")

if __name__ == "__main__":
    run_migration()
