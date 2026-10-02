"""
Main entry point for the FastAPI application.

This file initializes the application, configures middleware, and registers routers.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import (
    mapping_router,
    dbt_offline_router
)
from app.utils.logger import setup_logger  # Logger setup
from app.utils.error_handler import setup_error_handlers  # Custom error handling
from app.config import settings

# Initialize the FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered dbt code generation from source-to-target mapping specifications",
    debug=settings.DEBUG,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up logging
setup_logger()

# Set up custom error handling
setup_error_handlers(app)

# Register routers
app.include_router(mapping_router.router, prefix="/mappings", tags=["Mappings"])
app.include_router(dbt_offline_router.router, prefix="/dbt-offline", tags=["DBT Offline"])

@app.on_event("startup")
def startup_event():
    """
    On startup, check for any projects stuck in 'GENERATING' state and reset them to 'FAILED'.
    This prevents the UI from infinite polling if the server was restarted during generation.
    """
    from app.routers.mapping_router import SessionLocal, Project
    import logging
    logger = logging.getLogger(__name__)
    
    db = SessionLocal()
    try:
        stuck_projects = db.query(Project).filter(Project.dbt_status == "GENERATING").all()
        if stuck_projects:
            logger.warning(f"Found {len(stuck_projects)} projects stuck in GENERATING state. Resetting to FAILED.")
            for project in stuck_projects:
                logger.info(f"Resetting project {project.id} ({project.project_name}) status to FAILED")
                project.dbt_status = "FAILED"
            db.commit()
    except Exception as e:
        logger.error(f"Error resetting stuck projects: {e}")
    finally:
        db.close()

@app.get("/", tags=["Root"])
async def read_root():
    """
    Root endpoint to verify that the application is running.
    """
    return {"message": "Welcome to the DBT Code Generator API!"}