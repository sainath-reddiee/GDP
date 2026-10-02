"""
Database initialization script for Snowflake.

Note: This script creates tables using SQLAlchemy models.
Alternatively, you can run snowflake_setup.sql directly in Snowflake.
"""
from app.routers.mapping_router import Base
from sqlalchemy import create_engine
from snowflake.sqlalchemy import URL
from app.config import settings

def init_db():
    print("Creating Snowflake database tables...")
    
    # Create Snowflake engine
    engine = create_engine(URL(
        account=settings.SNOWFLAKE_ACCOUNT,
        user=settings.SNOWFLAKE_USER,
        password=settings.SNOWFLAKE_PASSWORD,
        database=settings.SNOWFLAKE_DATABASE,
        schema=settings.SNOWFLAKE_SCHEMA,
        warehouse=settings.SNOWFLAKE_WAREHOUSE,
        role=settings.SNOWFLAKE_ROLE,
    ))
    
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")

if __name__ == "__main__":
    init_db()
