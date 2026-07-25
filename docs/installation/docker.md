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

## What Are Docker Containers?

Think of a Docker container as a **boxed-up program** that includes everything it needs to run — code, libraries, settings, and dependencies. You don't install Python, PostgreSQL, or anything else manually. Docker handles it all.

iCAD Dispatch uses **3 containers** that work together:

### Container 1: `postgres` — The Database

**What it does:** Stores every call, transcript, address, and configuration setting.

**Why you need it:** Without the database, nothing is saved. The other two containers read from and write to this database.

**Image:** `postgis/postgis:16-3.4` (PostgreSQL 16 with PostGIS extension for map coordinates)

**Port:** `5432` (inside Docker only — never exposed to the internet)

**Data persistence:** Uses a Docker volume called `postgres_data`. Even if you delete the container, the data survives.

**Special notes:**
- Must start first and be healthy before the other containers start
- Only the other two containers need to talk to it
- **Never expose port 5432 to the internet** — it's for internal use only

---

### Container 2: `icad_dispatch` — The Main App

**What it does:** This is the brain of the operation. It:
- Accepts uploaded radio audio via the `/api/call-upload` endpoint
- Detects paging tones
- Transcribes speech using AI
- Extracts addresses from transcripts
- Classifies incident types
- Sends notifications to Discord, Telegram, Email, etc.
- Serves the admin dashboard

**Why you need it:** This is what you log into to manage everything.

**Image:** Built from the main `Dockerfile` in the repo

**Port:** `9911`

**Special notes:**
- Must be behind a reverse proxy (nginx/Caddy) with HTTPS in production
- Reads environment variables from `.env`
- Requires `postgres` to be healthy before it starts
- Needs `PUBLIC_MAP_API_KEY` to authenticate with the public map

---

### Container 3: `public_map` — The Public Live Map

**What it does:** Shows a real-time map of emergency calls that anyone can view.

**Why you need it:** This is what citizens and news outlets see. It displays calls on a map in real-time.

**Image:** Built from `public_map/Dockerfile`

**Port:** `5000`

**Special notes:**
- **Completely separate** from the main app — it cannot modify anything
- Has read-only database access
- Uses **Socket.IO** (WebSocket) to push new calls to browsers instantly
- Needs the same `PUBLIC_MAP_API_KEY` as the main app
- Must also be behind a reverse proxy with HTTPS

---

## How the 3 Containers Talk to Each Other

```
Internet
    │
    ▼
┌─────────────────────────────────────────┐
│         Reverse Proxy (Caddy/nginx)       │
│   Handles HTTPS and forwards requests     │
└─────────────────────────────────────────┘
    │                           │
    ▼                           ▼
┌──────────────┐      ┌──────────────┐
│ icad_dispatch │      │  public_map   │
│   (port 9911)  │      │  (port 5000)  │
└──────┬───────┘      └──────┬───────┘
       │                     │
       └──────────┬──────────┘
                  │
         ┌────────▼────────┐
         │     postgres      │
         │    (port 5432)    │
         └───────────────────┘
```

**Important:** All three containers share the same **Docker network**. They can talk to each other by name:
- `icad_dispatch` connects to `postgres` by hostname `postgres`
- `public_map` connects to `postgres` by hostname `postgres`
- `icad_dispatch` pushes calls to `public_map` via HTTP to `http://public_map:5000/api/push-call`

---

## Prerequisites

Before starting, you need:

- A Linux server (Ubuntu 22.04+ or Debian 12) with a public IP
- Docker 24.0+ and Docker Compose 2.0+
- Git
- A domain name (or subdomain) pointing to your server
- 4 GB RAM, 20 GB disk minimum
- Root or sudo access

### Check Docker

```bash
docker --version
docker compose version
```

If not installed:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

---

## Step-by-Step Setup

### Step 1: Clone Repository

```bash
cd ~
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git
cd icad_dispatch_v2
```

**What this does:** Downloads all the code, configs, and documentation.

---

### Step 2: Create Environment File

```bash
cp .env.example .env
nano .env
```

**What this does:** Copies the template to `.env` and opens it for editing.

**Required changes:**

| Variable | What it is | Example |
|---|---|---|
| `BASE_URL` | Your dashboard URL | `https://dispatch.yourdomain.com` |
| `TIMEZONE` | Your IANA timezone | `America/New_York` |
| `PG_PASSWORD` | Strong PostgreSQL password | `your-unique-password-here` |
| `PUBLIC_MAP_API_KEY` | Long random string (shared secret) | Generate with `openssl rand -hex 24` |
| `MAP_SECRET_KEY` | Different long random string | Generate with `openssl rand -hex 32` |
| `ROOT_PASSWORD` | Admin login password | `change-me-immediately` |

**Generate strong secrets:**
```bash
openssl rand -hex 24   # PUBLIC_MAP_API_KEY
openssl rand -hex 32   # MAP_SECRET_KEY
```

**Save in nano:**
1. Press `Ctrl + O`
2. Press `Enter`
3. Press `Ctrl + X`

For full details on all variables, see [Environment Variables](../configuration/env-variables.md).

---

### Step 3: Create Required Directories and User

The container runs as a non-root user (`UID 9911`). You must create this user on the host and set directory ownership so the container can write to its volumes.

```bash
# Create the user and group
sudo groupadd -g 9911 icad_dispatch 2>/dev/null || true
sudo useradd -M -s /usr/sbin/nologin -u 9911 -g icad_dispatch icad_dispatch 2>/dev/null || true

# Create directories
mkdir -p var log audio

# Set ownership
sudo chown -R 9911:9911 var log audio

# Allow yourself to manage files too
sudo usermod -aG icad_dispatch "$USER"
```

**What this does:**
- Creates the `icad_dispatch` user/group with UID/GID 9911
- Creates folders for runtime data, logs, and audio
- Sets ownership so the container (running as 9911) can read and write
- Adds your user to the `icad_dispatch` group so you can manage files

> **Note:** You may need to log out and back in for the group change to take effect.

---

### Step 4: Build Images

```bash
docker compose -f docker-compose.production.yml build
```

**What this does:**
1. Downloads PostgreSQL 16 + PostGIS
2. Builds the main app image from `Dockerfile`
3. Builds the public map image from `public_map/Dockerfile`

**How long it takes:** 5–10 minutes on first run (depends on internet speed).

**What you'll see:**
- Lots of download progress bars
- Python package installation
- "Successfully built icad_dispatch_v2:local"
- "Successfully built icad_public_map:local"

---

### Step 5: Start Services

```bash
docker compose -f docker-compose.production.yml up -d
```

**What this does:**
- Starts all three containers in the background (`-d` = detached)
- `postgres` starts first (it has a health check)
- `icad_dispatch` waits for `postgres` to be healthy
- `public_map` waits for `postgres` to be healthy

---

### Step 6: Verify

```bash
docker compose -f docker-compose.production.yml ps
```

**Expected output:**
```
NAME                              STATUS          PORTS
icad_dispatch_v2-postgres-1       Up 10s (healthy)   0.0.0.0:5432->5432/tcp
icad_dispatch_v2-icad_dispatch-1  Up 10s (healthy)   0.0.0.0:9911->9911/tcp
icad_dispatch_v2-public_map-1     Up 10s              0.0.0.0:5000->5000/tcp
```

**What each column means:**
- `NAME` — The container name (auto-generated by Docker Compose)
- `STATUS` — `Up` means running, `(healthy)` means the health check passed
- `PORTS` — Which ports are exposed to the host

---

### Step 7: Check Logs

If any container is not healthy, check its logs:

```bash
# Main application logs
docker compose -f docker-compose.production.yml logs -f icad_dispatch

# Public map logs
docker compose -f docker-compose.production.yml logs -f public_map

# Database logs
docker compose -f docker-compose.production.yml logs -f postgres
```

**Press `Ctrl + C`** to stop watching logs.

---

## Reverse Proxy Setup (Required for HTTPS)

**You must use HTTPS in production.** Browsers block audio and WebSocket connections over HTTP.

A reverse proxy handles HTTPS for you. It sits in front of your containers and forwards requests.

### Option A: Caddy (Easiest — Automatic HTTPS)

Caddy automatically gets and renews HTTPS certificates from Let's Encrypt.

```bash
sudo apt-get install caddy
sudo nano /etc/caddy/Caddyfile
```

Paste this (replace `yourdomain.com`):
```
dispatch.yourdomain.com {
    reverse_proxy localhost:9911
}

map.yourdomain.com {
    reverse_proxy localhost:5000
}
```

Save and reload:
```bash
sudo systemctl reload caddy
```

**What this does:**
- `dispatch.yourdomain.com` → forwards to main app on port 9911
- `map.yourdomain.com` → forwards to public map on port 5000
- Automatically obtains HTTPS certificates
- Auto-renews certificates before they expire

---

### Option B: nginx (More Control)

```bash
sudo apt-get install nginx
sudo nano /etc/nginx/sites-available/icad
```

Paste this:
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
    listen 80;
    server_name map.yourdomain.com;
    return 301 https://$server_name$request_uri;
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

**If you don't have SSL certificates yet**, use Let's Encrypt with Certbot:
```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d dispatch.yourdomain.com -d map.yourdomain.com
```

---

## Let's Encrypt (Free HTTPS)

### With Caddy

Caddy handles this automatically. No extra steps needed.

### With nginx + Certbot

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d dispatch.yourdomain.com -d map.yourdomain.com
```

Certbot will:
1. Verify you own the domains
2. Obtain certificates from Let's Encrypt
3. Configure nginx to use them
4. Set up auto-renewal

---

## Updating

To update to the latest version:

```bash
cd /path/to/icad_dispatch_v2
git pull origin main
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d
```

**What this does:**
1. Downloads the latest code from GitHub
2. Rebuilds the containers with new code
3. Restarts everything

**Database migrations run automatically** on startup. You don't need to do anything.

---

## Common Commands

### Move configuration to another instance

Use the [Configuration Migration](config-migration.md) guide to export and
import operational settings without copying call history or user passwords.

```bash
# View all running containers
docker compose -f docker-compose.production.yml ps

# View logs (follow mode)
docker compose -f docker-compose.production.yml logs -f icad_dispatch
docker compose -f docker-compose.production.yml logs -f public_map
docker compose -f docker-compose.production.yml logs -f postgres

# Restart a specific service
docker compose -f docker-compose.production.yml restart icad_dispatch
docker compose -f docker-compose.production.yml restart public_map

# Stop everything (keeps data)
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

### Build fails with "no space left on device"

Docker images take up disk space. Clean up old images:
```bash
docker system prune -a
```

**Warning:** This deletes all unused images, containers, and volumes.

---

### Port 9911 already in use

Something else is using port 9911:
```bash
sudo lsof -i :9911
# Or:
sudo ss -tlnp | grep 9911
```

**Fix:** Either kill the other process or change the port in `docker-compose.production.yml`:
```yaml
ports:
  - "9912:9911"  # Change 9911 to 9912 on the host side
```

Then update your reverse proxy to forward to `localhost:9912` instead.

---

### Database connection refused

The main app starts before the database is ready:
```bash
# Check database status
docker compose -f docker-compose.production.yml logs postgres

# If postgres is still starting, wait 30 seconds then restart the main app:
docker compose -f docker-compose.production.yml restart icad_dispatch
```

Also verify `PG_PASSWORD` in `.env` matches the actual database password.

---

### Public map not receiving calls

1. Check that `PUBLIC_MAP_API_KEY` is **identical** in `.env` for both containers
2. Check the main app logs for push errors:
   ```bash
   docker compose -f docker-compose.production.yml logs -f icad_dispatch
   ```
3. Verify the public map can reach the main app:
   ```bash
   docker exec icad_dispatch_v2-public_map-1 curl http://icad_dispatch:9911/health
   ```

---

### Can't access dashboard

1. Check the container is running:
   ```bash
   docker compose -f docker-compose.production.yml ps
   ```
2. Check logs for errors:
   ```bash
   docker compose -f docker-compose.production.yml logs icad_dispatch
   ```
3. Verify your reverse proxy is configured correctly
4. Make sure your domain DNS points to the server IP
5. Check firewall rules:
   ```bash
   sudo ufw status
   ```

---

*For alternative install methods, see [Native Installation](native.md) or [One-Click Installer](one-click.md).*
