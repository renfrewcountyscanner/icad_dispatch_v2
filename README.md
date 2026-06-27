# iCAD Dispatch v2

Real-time Emergency Services Dispatch System for Fire, EMS, and Public Safety agencies across North America.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue?logo=docker)](docs/installation/docker.md)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-green)](https://renfrewcountyscanner.github.io/icad_dispatch_v2/)

---

## What is iCAD Dispatch?

iCAD Dispatch v2 ingests real-time radio audio from emergency services, automatically:

- **Detects tones** (two-tone paging, long-tone, MDC, DTMF)
- **Transcribes speech** using Whisper AI
- **Extracts addresses** from transcripts using LLM + geocoding
- **Classifies incidents** (Fire, Medical, Traffic, Rescue, etc.)
- **Sends notifications** to Discord, Telegram, Email, Pushover, n8n, Make, and Ntfy
- **Displays calls** on a live public map with real-time updates

Built for **North American emergency services** — fire departments, EMS agencies, and dispatch centers.

---

## The 3 Containers (What They Do)

iCAD Dispatch runs as **3 separate Docker containers**. Think of them as 3 separate programs that talk to each other:

### 1. `postgres` — The Database

**What it does:** Stores every call, transcript, address, and configuration.

**Why you need it:** Without this, nothing is saved. The other two containers read from and write to this database.

**Exposed port:** `5432` (only inside Docker, not to the internet)

**Special notes:**
- Uses **PostgreSQL 16** with **PostGIS** (for map coordinates)
- Data is stored in a Docker volume called `postgres_data`
- **Never expose port 5432 to the internet** — only the other two containers need it

---

### 2. `icad_dispatch` — The Main App (Dashboard + API)

**What it does:** This is the brain. It processes radio calls, runs AI transcription, extracts addresses, and sends notifications.

**Why you need it:** This is what you log into. It has:
- Admin dashboard (port `9911`)
- Call upload API endpoint
- Tone detection, transcription, geocoding
- Notification dispatch (Discord, Telegram, Email, etc.)

**Exposed port:** `9911`

**Special notes:**
- Must be behind a reverse proxy (nginx/Caddy) with HTTPS in production
- Requires the `postgres` container to be healthy before it starts
- Needs environment variables from `.env` (see below)

---

### 3. `public_map` — The Public Live Map

**What it does:** Shows a real-time map of emergency calls that the public can view.

**Why you need it:** This is what citizens see. It reads call data from the database and pushes updates to browsers in real-time.

**Exposed port:** `5000`

**Special notes:**
- **Completely separate** from the main app — it cannot modify anything
- Has read-only access to the database
- Uses **Socket.IO** (WebSocket) to push new calls to browsers instantly
- Must also be behind a reverse proxy with HTTPS
- Needs the same `PUBLIC_MAP_API_KEY` as the main app (this is how they authenticate to each other)

---

## How They Talk to Each Other

```
┌─────────────────────────────────────────────────────────────┐
│                        INTERNET                               │
│                                                               │
│   YOU ──► https://dispatch.yourdomain.com  (main app: 9911)   │
│   PUBLIC ──► https://map.yourdomain.com    (public map: 5000)│
└─────────────────────────────────────────────────────────────┘
                            │
                    ┌───────▼───────┐
                    │  Reverse Proxy │  ← nginx or Caddy handles HTTPS
                    │   (port 443)   │
                    └───────┬───────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
     ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
     │ icad_dispatch│ │  public_map │ │   postgres   │
     │  (port 9911) │ │  (port 5000)│ │  (port 5432) │
     └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
            │               │               │
            └───────────────┴───────────────┘
                            │
                     All three containers
                     share the same Docker network
                     (they can talk to each other by name)
```

**Key point:** The main app (`icad_dispatch`) **pushes** new calls to the public map. The public map **polls** the database as a backup. The database is the single source of truth.

---

## Quick Start (15 Minutes)

### What You Need Before Starting

1. A **Linux server** (Ubuntu 22.04+ or Debian 12) with a public IP
2. **Docker** and **Docker Compose** installed
3. A **domain name** pointing to your server (e.g., `dispatch.yourdomain.com` and `map.yourdomain.com`)
4. Basic command-line knowledge (copy/paste commands)

### Step 1: Install Docker

If you don't have Docker yet, run this:

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in, or run: newgrp docker
```

Verify Docker is working:
```bash
docker --version
docker compose version
```

### Step 2: Download iCAD Dispatch

```bash
cd ~
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git
cd icad_dispatch_v2
```

> **Shortcut:** Run `./install.sh` for an interactive guided setup that creates users, generates secrets, builds containers, and starts everything automatically. See the [One-Click Installer](docs/installation/one-click.md).

### Step 3: Create Your Settings File

```bash
cp .env.example .env
nano .env
```

**You MUST change these 6 values:**

| Variable | What it is | Example |
|---|---|---|
| `BASE_URL` | Your dashboard URL | `https://dispatch.yourdomain.com` |
| `TIMEZONE` | Your timezone | `America/New_York` |
| `PG_PASSWORD` | Database password | `MyVerySecretPassword123!` |
| `PUBLIC_MAP_API_KEY` | Secret key shared between containers | `openssl rand -hex 24` |
| `MAP_SECRET_KEY` | Secret for the public map | `openssl rand -hex 32` |
| `ROOT_PASSWORD` | Your admin login password | `MyAdminPassword456!` |

**Generate random secrets:**
```bash
openssl rand -hex 24   # Use this for PUBLIC_MAP_API_KEY
openssl rand -hex 32   # Use this for MAP_SECRET_KEY
```

**Special note on `PUBLIC_MAP_API_KEY`:**
This is a **shared password** between the main app and the public map. Both containers must have the exact same value. If they don't match, the public map won't receive new calls.

### Step 4: Start Everything

```bash
docker compose -f docker-compose.production.yml up -d
```

This will:
1. Download PostgreSQL 16 + PostGIS
2. Build the main app container
3. Build the public map container
4. Start all three in the background

First run takes **5–10 minutes** because it downloads and builds everything.

### Step 5: Check That Everything Is Running

```bash
docker compose -f docker-compose.production.yml ps
```

You should see:
```
NAME                              STATUS          PORTS
icad_dispatch_v2-postgres-1       Up (healthy)    5432/tcp
icad_dispatch_v2-icad_dispatch-1  Up (healthy)    0.0.0.0:9911->9911/tcp
icad_dispatch_v2-public_map-1     Up              0.0.0.0:5000->5000/tcp
```

**If any container says `Exited` or `Restarting`, check the logs:**
```bash
docker compose -f docker-compose.production.yml logs -f icad_dispatch
docker compose -f docker-compose.production.yml logs -f public_map
docker compose -f docker-compose.production.yml logs -f postgres
```

### Step 6: Set Up HTTPS (Required for Production)

**You cannot use HTTP in production.** Browsers block audio and WebSocket over HTTP. You need a reverse proxy.

#### Option A: Caddy (Easiest — Automatic HTTPS)

```bash
sudo apt-get install caddy
sudo nano /etc/caddy/Caddyfile
```

Paste this (replace `yourdomain.com` with your actual domain):
```
dispatch.yourdomain.com {
    reverse_proxy localhost:9911
}

map.yourdomain.com {
    reverse_proxy localhost:5000
}
```

Reload Caddy:
```bash
sudo systemctl reload caddy
```

Caddy **automatically** gets HTTPS certificates from Let's Encrypt. Zero configuration.

#### Option B: nginx (More Control)

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

### Step 7: Log In and Change Your Password

1. Open `https://dispatch.yourdomain.com` in your browser
2. Login:
   - **Username:** `root`
   - **Password:** The `ROOT_PASSWORD` from your `.env` file
3. **Immediately change the password** via the user menu (top right)

---

## Upgrading

To upgrade to the latest version:

```bash
cd /opt/icad_dispatch
git pull origin main
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d
```

**What this does:**
1. Downloads the latest code from GitHub
2. Rebuilds containers with updated code
3. Restarts everything

Database migrations run automatically on startup.

**To upgrade from a version before v2.5 (before the entrypoint fix):**

```bash
chown -R 9911:9911 log/ var/ audio/
```

The new entrypoint script handles this automatically on subsequent starts.

---

## What to Do Next

1. **[Add Your Radio System](docs/quickstart.md#step-6-configure-your-system)** — Configure tone detection and upload settings
2. **[Enable Address Extraction](docs/quickstart.md#step-6-configure-your-system)** — Set up geocoding for your region
3. **[Set Up Notifiers](docs/configuration/)** — Discord, Telegram, Email, etc.
4. **[Test Upload](docs/quickstart.md#step-7-test-upload)** — Send a test call
5. **[Security Hardening](docs/security.md)** — Firewall, fail2ban, etc.

---

## Features

| Feature | Description |
|---------|-------------|
| **Tone Detection** | Automatic two-tone, long-tone, MDC, and DTMF detection |
| **AI Transcription** | OpenAI Whisper local or API-based speech-to-text |
| **Address Extraction** | LLM-powered address parsing with Nominatim + Google Maps geocoding |
| **Incident Classification** | AI-categorized incident types with confidence scores |
| **Multi-Channel Alerts** | Discord, Telegram, Email, Pushover, n8n, Make, Ntfy |
| **Live Public Map** | Real-time WebSocket map with dark mode, filters, and audio playback |
| **Map Corrections** | Drag-and-drop location correction via dashboard |
| **Call History** | Searchable database with configurable retention |
| **Security** | Rate limiting, CSRF protection, path traversal prevention |

---

## Documentation

- **[Quick Start](docs/quickstart.md)** — Full 15-minute setup guide
- **[Docker Installation](docs/installation/docker.md)** — Detailed Docker guide with every command explained
- **[One-Click Installer](docs/installation/one-click.md)** — Automated install script
- **[Environment Variables](docs/configuration/env-variables.md)** — Every config option explained
- **[Geocoding Setup](docs/configuration/geocoding.md)** — Nominatim + Google Maps configuration
- **[Notifier Setup](docs/configuration/)** — Discord, Telegram, Email, Pushover, n8n, Make, Ntfy
- **[Public Map](docs/configuration/public-map.md)** — Live map configuration
- **[Security](docs/security.md)** — Hardening checklist
- **[API Reference](docs/api.md)** — REST API documentation
- **[Troubleshooting](docs/troubleshooting.md)** — Common issues and fixes

---

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 LTS |
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB SSD | 50 GB SSD |
| Network | Public IP or reverse proxy | Dedicated server / VPS |
| Docker | 24.0+ | Latest |

---

## License

[MIT License](LICENSE) — free for personal and commercial use.

---

Built with ❤️ for first responders everywhere.
