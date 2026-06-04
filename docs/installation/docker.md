---
layout: default
title: Docker Installation
parent: Installation
nav_order: 1
---

# Docker Installation
{: .no_toc }

The recommended way to run iCAD Dispatch v2.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Docker Compose deploys three services:

| Service | Image | Purpose | Exposed Port |
|---------|-------|---------|--------------|
| `postgres` | `postgis/postgis:16-3.4` | Database | 5432 (internal) |
| `icad_dispatch` | `icad_dispatch_v2:local` | Main app | 9911 |
| `public_map` | `icad_public_map:local` | Live map | 5000 |

---

## Prerequisites

- Docker 24.0+
- Docker Compose 2.0+
- Git
- Domain name pointing to your server (optional but recommended)
- 4 GB RAM, 20 GB disk minimum

---

## Step-by-Step Setup

### 1. Clone Repository

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git
cd icad_dispatch_v2
```

### 2. Create Environment File

```bash
cp .env.example .env
nano .env
```

Edit the values as described in [Environment Variables](../configuration/env-variables.md).

### 3. Create Required Directories

```bash
mkdir -p var/public_maps log audio
```

### 4. Build Images

```bash
docker compose -f docker-compose.production.yml build
```

This builds:
- `icad_dispatch_v2:local` from the main Dockerfile
- `icad_public_map:local` from `public_map/Dockerfile`

First build takes 5–10 minutes depending on your connection.

### 5. Start Services

```bash
docker compose -f docker-compose.production.yml up -d
```

### 6. Verify

```bash
docker compose -f docker-compose.production.yml ps
```

Expected output:
```
NAME                          STATUS          PORTS
icad_dispatch_v2-postgres-1    Up 10s (healthy)   0.0.0.0:5432->5432/tcp
icad_dispatch_v2-icad_dispatch-1  Up 10s (healthy)   0.0.0.0:9911->9911/tcp
icad_dispatch_v2-public_map-1  Up 10s              0.0.0.0:5000->5000/tcp
```

---

## Reverse Proxy Setup

For production, place a reverse proxy in front of Docker:

### nginx

```bash
sudo apt-get install nginx
sudo nano /etc/nginx/sites-available/icad
```

```nginx
server {
    listen 80;
    server_name dispatch.yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name dispatch.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:9911;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 443 ssl http2;
    server_name map.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Enable:
```bash
sudo ln -s /etc/nginx/sites-available/icad /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Caddy (Easiest)

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

---

## Let's Encrypt (Free HTTPS)

### With Caddy

Caddy automatically obtains and renews certificates. Just use a real domain in the Caddyfile.

### With nginx + Certbot

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d dispatch.yourdomain.com -d map.yourdomain.com
```

---

## Updating

To update to the latest version:

```bash
cd /path/to/icad_dispatch_v2
git pull origin main
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d
```

Database migrations run automatically on startup.

---

## Common Commands

```bash
# View logs
docker compose -f docker-compose.production.yml logs -f icad_dispatch

# Restart a service
docker compose -f docker-compose.production.yml restart icad_dispatch

# Stop everything
docker compose -f docker-compose.production.yml down

# Stop and remove volumes (WARNING: deletes database)
docker compose -f docker-compose.production.yml down -v

# Enter database shell
docker exec -it icad_dispatch_v2-postgres-1 psql -U icad -d icad_dispatch

# Backup database
docker exec icad_dispatch_v2-postgres-1 pg_dump -U icad icad_dispatch > backup.sql

# Restore database
cat backup.sql | docker exec -i icad_dispatch_v2-postgres-1 psql -U icad -d icad_dispatch
```

---

## Troubleshooting

**Build fails with "no space left on device"**
```bash
docker system prune -a
```

**Port 9911 already in use**
```bash
sudo lsof -i :9911
# Kill the process or change the port in docker-compose.production.yml
```

**Database connection refused**
```bash
# Check postgres is healthy
docker compose -f docker-compose.production.yml logs postgres
# Verify PG_* env vars match
```

---

*For alternative install methods, see [Native Installation](native.md) or [One-Click Installer](one-click.md).*
