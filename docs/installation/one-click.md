---
layout: default
title: One-Click Installer
parent: Installation
nav_order: 3
---

# One-Click Installer
{: .no_toc }

The fastest way to get iCAD Dispatch running.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

The `install.sh` script automates the entire setup process. It:

1. Checks prerequisites (Docker, Docker Compose, Git)
2. Clones the repository
3. Prompts for configuration (domain, timezone, passwords)
4. Generates secure secrets automatically
5. Creates the `.env` file
6. Builds and starts all Docker containers
7. Prints a summary with next steps

---

## Requirements

- Fresh Ubuntu 22.04+ or Debian 12 server
- Root or sudo access
- Public IP address
- Domain name (recommended but not required)

---

## Run the Installer

### Option 1: Direct Download (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/main/install.sh | bash
```

### Option 2: Download First, Review, Then Run

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/main/install.sh -o install.sh
nano install.sh      # Review the script
bash install.sh
```

### Option 3: With Custom Install Directory

```bash
bash install.sh /opt/icad_dispatch
```

---

## What the Script Does

### 1. Prerequisite Check

Verifies Docker, Docker Compose, and Git are installed.

### 2. Clone Repository

Downloads iCAD Dispatch to `/opt/icad_dispatch` (or your chosen directory).

### 3. Interactive Configuration

The script asks for:

| Prompt | Default | Purpose |
|--------|---------|---------|
| Dispatch domain | `dispatch.YOUR_DOMAIN.COM` | Dashboard URL |
| Map domain | `map.YOUR_DOMAIN.COM` | Public map URL |
| Timezone | `America/New_York` | Your region's timezone |
| HTTPS? | `yes` | Enable secure cookies |
| PostgreSQL password | auto-generated | Database password |
| Google Maps API key | (optional) | Geocoding fallback |
| OpenAI API key | (optional) | LLM address extraction |

Secrets (`PUBLIC_MAP_API_KEY`, `MAP_SECRET_KEY`, `ROOT_PASSWORD`) are auto-generated.

### 4. Write Configuration

Creates `.env` with all your settings.

### 5. Build & Start

Runs:
```bash
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d
```

### 6. Print Summary

Outputs:
- Dashboard URL
- Public map URL
- Admin credentials
- Useful commands
- Next steps

---

## Post-Install

After the installer finishes:

### 1. Set Up HTTPS

If you have a domain, configure a reverse proxy:

**Caddy (easiest):**
```bash
sudo apt-get install caddy
sudo nano /etc/caddy/Caddyfile
```

```
dispatch.yourdomain.com {
    reverse_proxy localhost:9911
}

map.yourdomain.com {
    reverse_proxy localhost:5000
}
```

```bash
sudo systemctl reload caddy
```

**nginx:** See [Docker Installation](docker.md#reverse-proxy-setup).

### 2. Log In

1. Open `https://dispatch.yourdomain.com`
2. Login:
   - Username: `root`
   - Password: (shown in installer summary, also in `.env`)
3. **Change the password immediately**

### 3. Configure Your System

Follow the [Quick Start Guide](../quickstart.md#step-6-configure-your-system) to:
- Add your radio system
- Enable address extraction
- Set up notifiers
- Test upload

---

## Customizing the Installer

### Skip Interactive Prompts

Edit `install.sh` and hardcode values at the top:

```bash
# Near the top of install.sh, add:
DISPATCH_DOMAIN="https://dispatch.mydomain.com"
MAP_DOMAIN="https://map.mydomain.com"
TZ="America/Chicago"
DB_PASS="my-secure-password"
# ... etc
```

### Use Environment Variables

```bash
export ICAD_DOMAIN="dispatch.mydomain.com"
bash install.sh
```

Modify the script to read these variables if present.

---

## Troubleshooting

**"Docker is not installed"**
```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

**"Git clone failed"**
```bash
# Check internet connectivity
curl -I https://github.com
# Or clone manually:
git clone https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git /opt/icad_dispatch
```

**"Port 9911 already in use"**
```bash
sudo lsof -i :9911
# Change port in docker-compose.production.yml
```

---

## Security Note

The installer generates random secrets and stores the `.env` file with `chmod 600` (owner-only read). However, for production:

1. Change the auto-generated `ROOT_PASSWORD` immediately after first login
2. Move secrets to Docker secrets or a vault if possible
3. Enable UFW firewall: `sudo ufw enable && sudo ufw allow ssh && sudo ufw allow http && sudo ufw allow https`
4. See [Security Hardening](../security.md) for full checklist

---

*For manual setup, see [Docker Installation](docker.md) or [Native Installation](native.md).*
