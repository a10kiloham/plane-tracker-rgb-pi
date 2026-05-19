#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# Plane Tracker — Docker Build Helper Script
# ═══════════════════════════════════════════════════════════════════════════════
# This script simplifies building and running the Plane Tracker Docker image
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

IMAGE_NAME="plane-tracker"
IMAGE_TAG="latest"

# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ Functions                                                                    │
# └─────────────────────────────────────────────────────────────────────────────┘

print_header() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════════${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed"
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running"
        exit 1
    fi
    
    print_success "Docker is available"
}

check_env_file() {
    if [ ! -f .env ]; then
        print_warning ".env file not found"
        echo ""
        echo "Creating .env from .env.example..."
        cp .env.example .env
        print_info "Please edit .env with your configuration:"
        echo ""
        echo "  nano .env"
        echo ""
        echo "Required variables:"
        echo "  - FR24_API_KEY"
        echo "  - TOMORROW_API_KEY"
        echo "  - ZONE_TL_LAT, ZONE_TL_LON"
        echo "  - ZONE_BR_LAT, ZONE_BR_LON"
        echo "  - HOME_LAT, HOME_LON"
        echo "  - TEMPERATURE_LOCATION"
        echo ""
        read -p "Press Enter after configuring .env..."
    else
        print_success ".env file found"
    fi
}

build_image() {
    local build_args="$1"
    
    print_header "Building Docker Image"
    echo ""
    
    print_info "Image: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo ""
    
    if [ "$build_args" = "--no-cache" ]; then
        print_info "Building without cache (this may take a while)..."
        docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" . --no-cache
    else
        print_info "Building with cache..."
        docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" .
    fi
    
    echo ""
    print_success "Image built successfully"
}

start_service() {
    print_header "Starting Service"
    echo ""
    
    docker-compose up -d
    
    echo ""
    print_success "Service started"
    print_info "Web interface: http://localhost:6969"
    echo ""
    echo "View logs with:"
    echo "  docker-compose logs -f plane-tracker"
    echo ""
    echo "Stop with:"
    echo "  docker-compose down"
}

show_status() {
    print_header "Service Status"
    echo ""
    
    docker-compose ps
    echo ""
    
    if docker ps --filter "name=plane-tracker" --format "{{.Names}}" | grep -q plane-tracker; then
        print_success "Container is running"
        
        # Show health status
        local health=$(docker inspect --format='{{.State.Health.Status}}' plane-tracker 2>/dev/null || echo "unknown")
        echo ""
        echo "Health status: $health"
    else
        print_warning "Container is not running"
    fi
}

show_logs() {
    print_header "Service Logs"
    echo ""
    docker-compose logs --tail=50 -f plane-tracker
}

# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ Main Script                                                                  │
# └─────────────────────────────────────────────────────────────────────────────┘

main() {
    local command="${1:-help}"
    local build_args="${2:-}"
    
    case "$command" in
        build)
            check_docker
            check_env_file
            build_image "$build_args"
            ;;
        
        start)
            check_docker
            check_env_file
            start_service
            ;;
        
        restart)
            check_docker
            docker-compose restart plane-tracker
            print_success "Service restarted"
            ;;
        
        stop)
            check_docker
            docker-compose down
            print_success "Service stopped"
            ;;
        
        status)
            check_docker
            show_status
            ;;
        
        logs)
            check_docker
            show_logs
            ;;
        
        rebuild)
            check_docker
            check_env_file
            build_image "--no-cache"
            start_service
            ;;
        
        all)
            check_docker
            check_env_file
            build_image ""
            start_service
            ;;
        
        help|*)
            print_header "Plane Tracker — Build Helper"
            echo ""
            echo "Usage: ./build.sh <command> [options]"
            echo ""
            echo "Commands:"
            echo "  build [--no-cache]  Build the Docker image"
            echo "  start               Start the service"
            echo "  stop                Stop the service"
            echo "  restart             Restart the service"
            echo "  status              Show service status"
            echo "  logs                Follow service logs"
            echo "  rebuild             Rebuild without cache and restart"
            echo "  all                 Build and start in one command"
            echo "  help                Show this help message"
            echo ""
            echo "Examples:"
            echo "  ./build.sh all                  # Build and start"
            echo "  ./build.sh build                # Just build"
            echo "  ./build.sh build --no-cache     # Force rebuild"
            echo "  ./build.sh logs                 # View logs"
            echo ""
            ;;
    esac
}

main "$@"
