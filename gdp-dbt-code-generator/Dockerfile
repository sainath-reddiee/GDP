# Dockerfile for React Frontend + FastAPI Backend in single container
# Optimized for smaller image size using multi-stage builds

# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
# Install all dependencies (including dev dependencies needed for build)
RUN npm ci --silent
COPY frontend/ ./
ENV VITE_API_BASE_URL=/api
RUN npm run build && \
    rm -rf node_modules

# Stage 2: Final image with Python and nginx
FROM python:3.11-slim

# Install nginx and minimal build dependencies
# Build dependencies will be removed after Python package installation
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Create symlink for python
RUN ln -s /usr/bin/python3 /usr/bin/python

# Set working directory
WORKDIR /app

# Copy and install backend dependencies
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r backend/requirements.txt && \
    pip cache purge

# Remove build dependencies after installing Python packages (saves ~200MB)
RUN apt-get purge -y gcc g++ && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Copy backend application files
COPY backend/ ./backend/

# Copy frontend build from builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Expose port 80 (nginx reverse proxy)
EXPOSE 80
EXPOSE 443
EXPOSE 8000

# Start both services using the startup script
CMD ["/app/start.sh"]
