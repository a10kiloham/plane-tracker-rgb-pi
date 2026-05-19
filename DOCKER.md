# Plane Tracker — Docker Deployment Guide

This guide explains how to deploy the Plane Tracker application using Docker and Docker Compose.

## 📋 Prerequisites

- Docker Engine 20.10 or later
- Docker Compose v2.0 or later
- A `.env` file with your configuration (see below)

## 🚀 Quick Start

### 1. Configure Environment Variables

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
nano .env
```

**Required variables:**
- `FR24_API_KEY` — FlightRadar24 API key (format: `subscription_key|token`)
- `TOMORROW_API_KEY` — Tomorrow.io weather API key
- `ZONE_TL_LAT`, `ZONE_TL_LON` — Top-left corner of your detection zone
- `ZONE_BR_LAT`, `ZONE_BR_LON` — Bottom-right corner of your detection zone
- `HOME_LAT`, `HOME_LON` — Your home location coordinates
- `TEMPERATURE_LOCATION` — Weather location (format: `lat,lon`)

### 2. Build and Run

```bash
# Build the image
docker build -t plane-tracker:latest .

# Start the service
docker-compose up -d

# View logs
docker-compose logs -f plane-tracker
```

### 3. Access the Application

The web interface will be available at:
- **Internal**: `http://plane-tracker:6969` (for reverse proxy)
- **Direct**: `http://localhost:6969` (if ports are exposed)

## 🏗️ Architecture

### Multi-Stage Build

The Dockerfile uses a 3-stage build process for efficiency:

1. **Base Layer** — System dependencies (curl, unzip)
2. **Dependencies Layer** — Python packages from `requirements.txt`
3. **Application Layer** — Application code and airline logos

This structure ensures that:
- Minor code changes don't rebuild Python dependencies
- Docker cache is used effectively
- Build times are minimized

### Port Configuration

- **Container Port**: 6969
- **Exposed Port**: 6969 (configurable in `docker-compose.yml`)

The application is designed to run behind a reverse proxy (e.g., nginx, Traefik) that handles authentication.

## 📁 Persistent Data

Flight tracking data is stored in a named volume:

```yaml
volumes:
  plane-tracker-data:/var/lib/plane-tracker
```

This persists:
- Closest flight records (`close.txt`)
- Farthest flight records (`farthest.txt`)
- Currently tracked flight (`tracked_flight.json`)
- Generated maps

## 🔧 Configuration

### Environment Variables

All configuration is done via environment variables in the `.env` file. See `.env.example` for all available options.

### Docker Compose Override

To customize the deployment, create a `docker-compose.override.yml`:

```yaml
version: '3.8'

services:
  plane-tracker:
    # Custom port binding
    ports:
      - "8080:6969"
    
    # Additional environment variables
    environment:
      - BRIGHTNESS=80
      - DISTANCE_UNITS=imperial
    
    # Custom resource limits
    deploy:
      resources:
        limits:
          memory: 1G
```

## 🌐 Reverse Proxy Setup

### Nginx Example

```nginx
server {
    listen 80;
    server_name planes.example.com;

    location / {
        proxy_pass http://plane-tracker:6969;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Traefik Example

```yaml
services:
  plane-tracker:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.plane-tracker.rule=Host(`planes.example.com`)"
      - "traefik.http.services.plane-tracker.loadbalancer.server.port=6969"
```

## 🛠️ Commands

### Build

```bash
# Build with cache
docker build -t plane-tracker:latest .

# Build without cache (force rebuild)
docker build -t plane-tracker:latest . --no-cache

# Build with BuildKit for parallel builds
DOCKER_BUILDKIT=1 docker build -t plane-tracker:latest .
```

### Run

```bash
# Start in detached mode
docker-compose up -d

# Start and follow logs
docker-compose up

# Restart service
docker-compose restart plane-tracker

# Stop service
docker-compose down

# Stop and remove volumes
docker-compose down -v
```

### Logs & Debugging

```bash
# View logs
docker-compose logs plane-tracker

# Follow logs (live)
docker-compose logs -f plane-tracker

# View last 100 lines
docker-compose logs --tail=100 plane-tracker

# Execute shell in running container
docker-compose exec plane-tracker /bin/bash

# Check container status
docker-compose ps
```

### Updates

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose up -d --build
```

## 🔍 Health Check

The container includes a health check that runs every 30 seconds:

```bash
# View container health status
docker inspect --format='{{.State.Health.Status}}' plane-tracker

# View health check logs
docker inspect --format='{{range .State.Health.Log}}{{.Output}}{{end}}' plane-tracker
```

## 🐛 Troubleshooting

### Container won't start

1. Check environment variables:
   ```bash
   docker-compose config
   ```

2. View startup logs:
   ```bash
   docker-compose logs plane-tracker
   ```

3. Verify required variables are set:
   ```bash
   docker-compose exec plane-tracker env | grep -E "FR24_API_KEY|TOMORROW_API_KEY"
   ```

### Web interface not accessible

1. Check if container is running:
   ```bash
   docker-compose ps
   ```

2. Check health status:
   ```bash
   docker inspect --format='{{.State.Health.Status}}' plane-tracker
   ```

3. Test from inside container:
   ```bash
   docker-compose exec plane-tracker curl -f http://localhost:6969/
   ```

### Missing flight data

1. Verify API keys are correctly set in `.env`
2. Check that location coordinates are valid
3. Review logs for API errors:
   ```bash
   docker-compose logs -f plane-tracker | grep -i error
   ```

## 📊 Resource Usage

Typical resource consumption:
- **Memory**: 256-512 MB
- **CPU**: 0.5-1.0 core
- **Disk**: ~500 MB (image) + data volume

Adjust limits in `docker-compose.yml` as needed.

## 🔒 Security Notes

- The application does not include authentication
- Use a reverse proxy with authentication (nginx, Traefik, Caddy)
- Keep API keys secure in `.env` (never commit to git)
- The `.env` file is excluded in `.gitignore`

## 📦 What's Different from Pi Setup?

This Docker setup differs from the Raspberry Pi setup in:

1. **No RGB Matrix**: Runs web server only (not the LED display)
2. **No GPIO**: No hardware dependencies
3. **Portable**: Runs on any Docker-compatible system
4. **Simplified**: No system-level dependencies or compilation

The Raspberry Pi setup in `its-a-plane-python/setup/update-pi.sh` is still needed for the physical LED matrix display.
