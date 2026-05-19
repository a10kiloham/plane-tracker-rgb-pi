# ═══════════════════════════════════════════════════════════════════════════════
# Plane Tracker — Multi-stage Docker Image
# ═══════════════════════════════════════════════════════════════════════════════
# Build with: docker build -t plane-tracker:latest .
# Run with: docker-compose up -d
# ═══════════════════════════════════════════════════════════════════════════════

# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ Stage 1: Base Dependencies Layer                                            │
# └─────────────────────────────────────────────────────────────────────────────┘
FROM python:3.11-slim-bookworm AS base

# Install system dependencies in a single layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        unzip \
        && \
    rm -rf /var/lib/apt/lists/*

# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ Stage 2: Python Dependencies Layer                                          │
# └─────────────────────────────────────────────────────────────────────────────┘
FROM base AS dependencies

# Set working directory
WORKDIR /app

# Copy only requirements first (cache layer if requirements don't change)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ Stage 3: Application Layer                                                  │
# └─────────────────────────────────────────────────────────────────────────────┘
FROM dependencies AS application

# Copy application code
COPY its-a-plane-python/ ./its-a-plane-python/
COPY icons/ ./icons/

# Extract airline logos if logo.zip exists
COPY logo.zip* ./
RUN if [ -f logo.zip ]; then \
        mkdir -p its-a-plane-python/logos && \
        unzip -qo logo.zip -d its-a-plane-python/logos && \
        chmod -R a+r its-a-plane-python/logos && \
        rm logo.zip; \
    fi

# Copy environment example (user will override with docker-compose)
COPY .env.example .

# Create data directory for persistent storage
RUN mkdir -p /var/lib/plane-tracker/maps && \
    chmod -R 755 /var/lib/plane-tracker

# Set environment variable for data directory
ENV PLANE_TRACKER_DATA_DIR=/var/lib/plane-tracker

# Expose web server port
EXPOSE 6969

# Set environment variable for Flask to bind to correct port
ENV FLASK_RUN_PORT=6969

# Set working directory to app location
WORKDIR /app/its-a-plane-python

# Health check to verify the web server is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:6969/ || exit 1

# Run the web server on port 6969 (override default 8080)
CMD ["python3", "-c", "from web.app import app; app.run(host='0.0.0.0', port=6969, debug=False)"]
