import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add src to pythonpath
base_dir = Path(__file__).resolve().parent
src_dir = base_dir / "src"
sys.path.append(str(src_dir))

from extract.profile_real_data import get_connection, list_tables

load_dotenv(src_dir / ".env")

def test_explicit_conn():
    print("Testing explicit connection parameters...")
    params = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "role": os.getenv("SNOWFLAKE_ROLE"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA"),
    }
    
    # Intentionally clear env vars to ensure kwargs are used
    os.environ["SNOWFLAKE_ACCOUNT"] = ""
    os.environ["SNOWFLAKE_USER"] = ""
    
    try:
        conn = get_connection(**params)
        print("✅ Connection with explicit params successful!")
        
        tables = list_tables(conn, limit=5)
        print(f"✅ Found {len(tables)} tables: {tables}")
        
        conn.close()
    except Exception as e:
        print(f"❌ Explicit connection test failed: {e}")

if __name__ == "__main__":
    test_explicit_conn()
