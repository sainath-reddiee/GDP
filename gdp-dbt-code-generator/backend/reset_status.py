import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the backend directory BEFORE importing settings
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config.settings import settings
from app.routers.mapping_router import Project
from snowflake.sqlalchemy import URL

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
SessionLocal = sessionmaker(bind=engine)

def reset_project_status(project_id):
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            print(f"Current status for Project {project_id}: {project.dbt_status}")
            if project.dbt_status == "GENERATING":
                print("Status is stuck in GENERATING. Resetting to FAILED...")
                project.dbt_status = "FAILED"
                db.commit()
                print("Status reset successful.")
            else:
                print("Status is not GENERATING. No action needed.")
        else:
            print(f"Project {project_id} not found.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    reset_project_status(201)
