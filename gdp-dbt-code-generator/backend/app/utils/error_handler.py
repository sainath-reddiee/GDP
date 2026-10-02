"""
Custom error handling for the FastAPI application.
 
This module provides middleware and utility functions for handling
errors and returning standardized error responses.
"""
 
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
import logging
from fastapi.exceptions import RequestValidationError
 
 
def setup_error_handlers(app: FastAPI):
    """
    Attach custom error handling middleware to the FastAPI application.
 
    Args:
        app (FastAPI): The FastAPI application instance.
    """
 
    @app.middleware("http")
    async def custom_error_middleware(request: Request, call_next):
        """
        Middleware to catch unhandled exceptions and provide standardized responses.
 
        Args:
            request (Request): The incoming HTTP request.
            call_next: Callable to forward the request to the next process.
 
        Returns:
            JSONResponse: Standardized error response in case of unhandled exceptions.
        """
        try:
            return await call_next(request)
        except Exception as exc:
            logging.error(f"Unhandled exception: {exc}", exc_info=True)
            try:
                # Optimized for multipart/form-data which often fails with .json()
                content_type = request.headers.get("content-type", "")
                if request.method in ["POST", "PUT", "PATCH"]:
                    if "application/json" in content_type:
                        payload = await request.json()
                    else:
                        payload = f"Non-JSON content: {content_type}"
                elif request.method == "GET":
                    payload = dict(request.query_params)
                else:
                    payload = None
            except Exception:
                payload = None
            content = {
                "success": False,
                "payload": payload,
                "error": {
                    "code": 500,
                    "message": "An internal server error occurred.",
                    "details": str(exc)
                },
            }
            return JSONResponse(status_code=500, content=jsonable_encoder(content))
 
 
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        try:
            if request.method in ["POST", "PUT", "PATCH"]:
                payload = await request.json()
            elif request.method == "GET":
                payload = dict(request.query_params)
            else:
                payload = None
        except Exception:
            payload = None
        content = {
            "success": False,
            "payload": payload,
            "error": {
                "code": exc.status_code,
                "message": str(exc.detail),
                "details": str(exc)
            },
        }
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(content))
 
 
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        content = {
            "success": False,
            "payload": {},
            "error": {
                "code": 422,
                "message": "Validation error",
                "details": exc.errors()
            },
        }
        return JSONResponse(status_code=422, content=jsonable_encoder(content))