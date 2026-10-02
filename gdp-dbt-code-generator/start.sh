#!/bin/bash

# Start nginx in the background
nginx

# Start uvicorn in the foreground
cd /app/backend
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

