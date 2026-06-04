#!/usr/bin/env bash
# install.sh — One-Click Installer for iCAD Dispatch v2
# ─────────────────────────────────────────────────────────────────────────────
# This script automates the setup of iCAD Dispatch v2 on a fresh Linux server.
# It assumes Docker and Docker Compose are already installed.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/YOUR_USER/icad_dispatch_v2/main/install.sh | bash
#   # or download and run locally:
#   bash install.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git"
INSTALL_DIR="${1:-/opt/icad_dispatch}"

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }

# ─── Helpers ─────────────────────────────────────────────────────────────────

prompt() {
    local msg="$1"
    local default="${2:-}"
    if [ -n "$default" ]; then
        read -rp "$msg [$default]: " val
        echo "${val:-$default}"
    else
        read -rp "$msg: " val
        echo "$val"
    fi
}

prompt_password() {
    local msg="$1"
    local val
    read -rsp "$msg: " val
    echo
    echo "$val"
}

generate_secret() {
    openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | xxd -p | tr -d '\n' | head -c 64
}

generate_api_key() {
    openssl rand -hex 24 2>/dev/null || head -c 48 /dev/urandom | xxd -p | tr -d '\n' | head -c 48
}

# ─── Prerequisites ───────────────────────────────────────────────────────────

check_prereqs() {
    log_info "Checking prerequisites..."

    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed. Please install Docker first:"
        log_error "  https://docs.docker.com/engine/install/"
        exit 1
    fi

    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
        log_error "Docker Compose is not installed. Please install it first:"
        log_error "  https://docs.docker.com/compose/install/"
        exit 1
    fi

    if ! command -v git &>/dev/null; then
        log_error "Git is not installed. Please install it first:"
        log_error "  sudo apt-get install git   # Debian/Ubuntu"
        log_error "  sudo yum install git       # RHEL/CentOS"
        exit 1
    fi

    log_ok "Prerequisites OK (Docker, Docker Compose, Git)"
}

# ─── Clone Repository ────────────────────────────────────────────────────────

clone_repo() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        log_warn "Directory $INSTALL_DIR already exists and is a git repo."
        log_info "Pulling latest changes..."
        cd "$INSTALL_DIR"
        git pull origin main || true
    else
        log_info "Cloning repository to $INSTALL_DIR..."
        git clone "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi
}

# ─── Interactive Configuration ──────────────────────────────────────────────

configure() {
    log_info "Configuration — press Enter to accept defaults shown in [brackets]."
    echo

    # Domain
    DISPATCH_DOMAIN=$(prompt "Enter your dispatch dashboard domain" "dispatch.YOUR_DOMAIN.COM")
    MAP_DOMAIN=$(prompt "Enter your public map domain" "map.$DISPATCH_DOMAIN")

    # Timezone
    echo
    log_info "Common North American timezones:"
    echo "  America/New_York (Eastern)"
    echo "  America/Chicago (Central)"
    echo "  America/Denver (Mountain)"
    echo "  America/Los_Angeles (Pacific)"
    echo "  America/Anchorage (Alaska)"
    echo "  America/Halifax (Atlantic)"
    TZ=$(prompt "Enter your IANA timezone" "America/New_York")

    # HTTPS
    echo
    USE_HTTPS=$(prompt "Will you use HTTPS? (yes/no)" "yes")
    if [[ "$USE_HTTPS" =~ ^[Yy] ]]; then
        COOKIE_SECURE="True"
        COOKIE_DOMAIN="${DISPATCH_DOMAIN#https://}"
        COOKIE_DOMAIN="${COOKIE_DOMAIN#http://}"
    else
        COOKIE_SECURE="False"
        COOKIE_DOMAIN="${DISPATCH_DOMAIN#https://}"
        COOKIE_DOMAIN="${COOKIE_DOMAIN#http://}"
    fi

    # Database password
    echo
    DB_PASS=$(prompt_password "Enter PostgreSQL password (or press Enter for auto-generated)")
    if [ -z "$DB_PASS" ]; then
        DB_PASS=$(generate_secret)
        log_ok "Auto-generated PostgreSQL password"
    fi

    # Secrets
    PUBLIC_MAP_API_KEY=$(generate_api_key)
    MAP_SECRET_KEY=$(generate_secret)
    ROOT_PASSWORD=$(generate_secret)

    # Optional: Google Maps
    echo
    USE_GOOGLE=$(prompt "Enable Google Maps geocoding fallback? (yes/no)" "no")
    GOOGLE_KEY=""
    if [[ "$USE_GOOGLE" =~ ^[Yy] ]]; then
        GOOGLE_KEY=$(prompt "Enter Google Maps API key")
    fi

    # Optional: OpenAI
    echo
    USE_OPENAI=$(prompt "Enable OpenAI address extraction? (yes/no)" "no")
    OPENAI_KEY=""
    if [[ "$USE_OPENAI" =~ ^[Yy] ]]; then
        OPENAI_KEY=$(prompt "Enter OpenAI API key")
    fi

    # Write .env
    cat > "$INSTALL_DIR/.env" <<EOF
# ─── Generated by install.sh on $(date +%Y-%m-%d) ───

LOG_LEVEL=2
TIMEZONE=$TZ
BASE_URL=$DISPATCH_DOMAIN

SESSION_COOKIE_SECURE=$COOKIE_SECURE
SESSION_COOKIE_DOMAIN=$COOKIE_DOMAIN
SESSION_COOKIE_NAME=icad_dispatch
SESSION_COOKIE_PATH=/

PG_HOST=postgres
PG_PORT=5432
PG_DATABASE=icad_dispatch
PG_USER=icad
PG_PASSWORD=$DB_PASS

PUBLIC_MAP_API_KEY=$PUBLIC_MAP_API_KEY
MAP_SECRET_KEY=$MAP_SECRET_KEY
PUBLIC_MAP_BASE_URL=$MAP_DOMAIN

ROOT_USERNAME=root
ROOT_PASSWORD=$ROOT_PASSWORD

# Optional notifiers — configure later via dashboard
# GOOGLE_MAPS_API_KEY=$GOOGLE_KEY
# OPENAI_API_KEY=$OPENAI_KEY
EOF

    # Update docker-compose.production.yml placeholders
    sed -i "s|https://map\.firepage\.ca|$MAP_DOMAIN|g" "$INSTALL_DIR/docker-compose.production.yml" 2>/dev/null || true
    sed -i "s|https://map\.yoursite\.com|$MAP_DOMAIN|g" "$INSTALL_DIR/docker-compose.production.yml" 2>/dev/null || true

    log_ok ".env file created at $INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
}

# ─── Directory Setup ────────────────────────────────────────────────────────

setup_dirs() {
    log_info "Creating required directories..."
    mkdir -p "$INSTALL_DIR"/var/public_maps
    mkdir -p "$INSTALL_DIR"/log
    mkdir -p "$INSTALL_DIR"/audio
    log_ok "Directories created"
}

# ─── Build & Start ───────────────────────────────────────────────────────────

build_and_start() {
    log_info "Building Docker images... (this may take a few minutes)"
    cd "$INSTALL_DIR"
    docker compose -f docker-compose.production.yml build

    log_info "Starting services..."
    docker compose -f docker-compose.production.yml up -d

    log_info "Waiting for services to become healthy..."
    sleep 10

    # Check health
    if docker compose -f docker-compose.production.yml ps | grep -q "healthy"; then
        log_ok "Services are healthy!"
    else
        log_warn "Services are still starting. Check status with:"
        log_warn "  docker compose -f docker-compose.production.yml ps"
    fi
}

# ─── Summary ─────────────────────────────────────────────────────────────────

print_summary() {
    echo
    echo "═══════════════════════════════════════════════════════════════"
    echo "  iCAD Dispatch v2 — Installation Complete!"
    echo "═══════════════════════════════════════════════════════════════"
    echo
    echo "  Dashboard:  $DISPATCH_DOMAIN"
    echo "  Public Map: $MAP_DOMAIN"
    echo "  Install Dir: $INSTALL_DIR"
    echo
    echo "  Admin Login:"
    echo "    Username: root"
    echo "    Password: (in .env file)"
    echo
    echo "  Next Steps:"
    echo "    1. Set up a reverse proxy (nginx/Caddy) for HTTPS"
    echo "    2. Configure notifiers in the dashboard"
    echo "    3. Upload your first test call"
    echo
    echo "  Useful Commands:"
    echo "    cd $INSTALL_DIR"
    echo "    docker compose -f docker-compose.production.yml logs -f"
    echo "    docker compose -f docker-compose.production.yml ps"
    echo
    echo "  Docs: https://YOUR_GITHUB_USERNAME.github.io/icad_dispatch_v2/"
    echo "═══════════════════════════════════════════════════════════════"
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    echo "═══════════════════════════════════════════════════════════════"
    echo "  iCAD Dispatch v2 — One-Click Installer"
    echo "═══════════════════════════════════════════════════════════════"
    echo

    check_prereqs
    clone_repo
    setup_dirs
    configure
    build_and_start
    print_summary
}

main "$@"
