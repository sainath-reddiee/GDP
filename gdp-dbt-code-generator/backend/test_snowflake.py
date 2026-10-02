"""
Test script to verify Snowflake connection and table structure.
Run this to debug connection issues.
"""
from sqlalchemy import create_engine, inspect
from snowflake.sqlalchemy import URL
from app.config import settings

def test_snowflake_connection():
    print("Testing Snowflake connection...")
    print(f"Account: {settings.SNOWFLAKE_ACCOUNT}")
    print(f"User: {settings.SNOWFLAKE_USER}")
    print(f"Database: {settings.SNOWFLAKE_DATABASE}")
    print(f"Schema: {settings.SNOWFLAKE_SCHEMA}")
    print(f"Warehouse: {settings.SNOWFLAKE_WAREHOUSE}")
    print(f"Role: {settings.SNOWFLAKE_ROLE}")
    print()
    
    try:
        # Create engine
        engine = create_engine(URL(
            account=settings.SNOWFLAKE_ACCOUNT,
            user=settings.SNOWFLAKE_USER,
            password=settings.SNOWFLAKE_PASSWORD,
            database=settings.SNOWFLAKE_DATABASE,
            schema=settings.SNOWFLAKE_SCHEMA,
            warehouse=settings.SNOWFLAKE_WAREHOUSE,
            role=settings.SNOWFLAKE_ROLE,
        ))
        
        print("✓ Engine created successfully")
        
        # Test connection
        with engine.connect() as conn:
            print("✓ Connected to Snowflake successfully")
            
            # Get inspector
            inspector = inspect(engine)
            
            # List all tables
            tables = inspector.get_table_names()
            print(f"\nTables found in {settings.SNOWFLAKE_DATABASE}.{settings.SNOWFLAKE_SCHEMA}:")
            for table in tables:
                print(f"  - {table}")
                
                # Get columns for each table
                columns = inspector.get_columns(table)
                print(f"    Columns:")
                for col in columns:
                    print(f"      - {col['name']} ({col['type']})")
            
            if not tables:
                print("  (No tables found)")
                print("\n⚠ WARNING: No tables found. Please run snowflake_setup_corrected.sql")
            
        print("\n✓ All checks passed!")
        
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_snowflake_connection()
