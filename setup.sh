#!/bin/bash
#
# iCAD Dispatch Setup Script
# One-command deployment for iCAD Dispatch
#
# Usage:
#   ./setup.sh              # Interactive mode
#   ./setup.sh --update     # Update existing installation
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
REPO_URL="https://github.com/renfrewcountyscanner/icad_dispatch_v2.git"
APP_DIR="/opt/icad_dispatch"
PORT="9911"

print_status() {
    echo -e "${GREEN}[+]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[!]${NC} $1"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker compose &> /dev/null && ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    # Check if Docker daemon is running
    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running. Please start Docker."
        exit 1
    fi
    
    print_status "Docker is available"
}

create_user() {
    if ! getent group icad_dispatch &> /dev/null; then
        print_status "Creating icad_dispatch group (GID 9911)..."
        sudo groupadd -g 9911 icad_dispatch || true
    fi
    
    if ! getent passwd icad_dispatch &> /dev/null; then
        print_status "Creating icad_dispatch user (UID 9911)..."
        sudo useradd -M -s /usr/sbin/nologin -u 9911 -g icad_dispatch icad_dispatch || true
    fi
    
    print_status "User and group ready"
}

prompt_values() {
    echo ""
    print_status "Configuration"
    echo "----------------"
    
    # Detect IP
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    
    read -p "Server IP or hostname [$DETECTED_IP]: " BASE_URL_IP
    BASE_URL_IP=${BASE_URL_IP:-$DETECTED_IP}
    
    read -p "Port [$PORT]: " APP_PORT
    APP_PORT=${APP_PORT:-$PORT}
    
    read -s -p "Admin password (changeme123 default): " ADMIN_PASS
    ADMIN_PASS=${ADMIN_PASS:-changeme123}
    echo ""
    
    read -p "Timezone [America/New_York]: " TZONE
    TZONE=${TZONE:-America/New_York}
    
    BASE_URL="http://${BASE_URL_IP}:${APP_PORT}"
}

setup_directory() {
    print_status "Setting up directory structure..."
    
    mkdir -p "$APP_DIR/log"
    mkdir -p "$APP_DIR/var"
    mkdir -p "$APP_DIR/audio"
    
    # Set permissions
    sudo chown -R icad_dispatch:icad_dispatch "$APP_DIR/log"
    sudo chown -R icad_dispatch:icad_dispatch "$APP_DIR/var"
    sudo chown -R icad_dispatch:icad_dispatch "$APP_DIR/audio"
    
    # Allow current user to manage files
    CURRENT_USER=$(whoami)
    sudo usermod -aG icad_dispatch "$CURRENT_USER" 2>/dev/null || true
    
    print_status "Directory structure ready"
}

create_env() {
    print_status "Creating configuration..."
    
    cat > "$APP_DIR/.env" << EOF
# ─────────────── Logging ───────────────
LOG_LEVEL=1

# ─────────────── Timezone IANA ───────────────
TIMEZONE=$TZONE

# ─────────────── Base URL ───────────────
BASE_URL=$BASE_URL

# ─────────────── Cookies ───────────────
SESSION_COOKIE_SECURE=False
SESSION_COOKIE_DOMAIN=$BASE_URL_IP
SESSION_COOKIE_NAME=icad_dispatch
SESSION_COOKIE_PATH=/

# ─────────────── SQLite ───────────────
SQLITE_DATABASE_PATH=var/icad_dispatch.db

# ─────────────── Root User Bootstrap ───────────────
ROOT_USERNAME=root
ROOT_PASSWORD=$ADMIN_PASS
EOF
    
    sudo chown icad_dispatch:icad_dispatch "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    
    print_status "Configuration created at $APP_DIR/.env"
}

start_container() {
    print_status "Starting iCAD Dispatch..."
    
    cd "$APP_DIR"
    
    # Pull latest image
    docker compose pull
    
    # Start container
    docker compose up -d
    
    print_status "iCAD Dispatch is starting..."
    sleep 3
    
    # Check status
    if docker ps | grep -q icad_dispatch; then
        echo ""
        print_status "============================================"
        print_status "iCAD Dispatch v2.1.0 is running!"
        print_status "============================================"
        echo ""
        print_status "Access at: $BASE_URL"
        print_status "Log in with: root / $ADMIN_PASS"
        echo ""
        print_warning "Change your password after first login!"
        echo ""
    else
        print_error "Container failed to start. Check logs with: docker logs icad_dispatch"
        exit 1
    fi
}

update_installation() {
    if [ ! -d "$APP_DIR" ]; then
        print_error "No installation found at $APP_DIR"
        exit 1
    fi
    
    print_status "Updating iCAD Dispatch..."
    cd "$APP_DIR"
    
    # Pull latest image
    docker compose pull
    
    # Restart
    docker compose restart
    
    print_status "Update complete!"
}

show_help() {
    echo "iCAD Dispatch Setup Script"
    echo ""
    echo "Usage:"
    echo "  ./setup.sh              Interactive setup"
    echo "  ./setup.sh --update     Update existing installation"
    echo "  ./setup.sh --help       Show this help"
    echo ""
}

# Main
case "${1:-}" in
    --update)
        check_docker
        update_installation
        ;;
    --help|-h)
        show_help
        ;;
    "")
        check_docker
        create_user
        prompt_values
        setup_directory
        create_env
        start_container
        ;;
    *)
        print_error "Unknown option: $1"
        show_help
        exit 1
        ;;
esac