---
layout: default
title: Quick Start
nav_order: 2
---

# Quick Start
{: .no_toc }

Get iCAD Dispatch v2 running in **15 minutes** with Docker Compose.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Prerequisites

Before starting, ensure you have:

- A Linux server (Ubuntu 22.04+ recommended) with a public IP or domain
- Docker 24.0+ and Docker Compose 2.0+ installed
- A domain name (or subdomain) pointing to your server
- Root or sudo access

### Check Docker

```bash
docker --version
docker compose version
```

If not installed, follow the [Docker installation guide](https://docs.docker.com/engine/install/ubuntu/).

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git
cd icad_dispatch_v2
```

---

## Step 2: Configure Environment

Copy the example environment file and edit it:

```bash
cp .env.example .env
nano .env
```

### Required Changes

| Variable | What to set | Example |
|----------|-------------|---------|
| `BASE_URL` | Your dispatch dashboard URL | `https://dispatch.yourdomain.com` |
| `TIMEZONE` | Your IANA timezone | `America/New_York` |
| `PG_PASSWORD` | Strong PostgreSQL password | `your-unique-password-here` |
| `PUBLIC_MAP_API_KEY` | Long random string | `change-me-to-a-long-random-api-key` |
| `MAP_SECRET_KEY` | Different long random string | `another-long-random-string` |
| `ROOT_PASSWORD` | Admin login password | `change-me-immediately` |

### Optional Changes

| Variable | Description |
|----------|-------------|
| `SESSION_COOKIE_DOMAIN` | Should match your `BASE_URL` domain |
| `SESSION_COOKIE_SECURE` | Set `True` when using HTTPS |
| `GOOGLE_MAPS_API_KEY` | For Google geocoding fallback |
| `OPENAI_API_KEY` | For LLM-based address extraction |

### Generate Strong Secrets

```bash
# API key (48 hex chars)
openssl rand -hex 24

# Secret key (64 hex chars)
openssl rand -hex 32
```

---

## Step 3: Build and Start

```bash
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d
```

This builds three Docker images:
- `postgres` — PostgreSQL 16 + PostGIS database
- `icad_dispatch` — Main application server
- `public_map` — Live public map service

---

## Step 4: Verify Services

```bash
docker compose -f docker-compose.production.yml ps
```

You should see all three services as `Up` and `healthy`.

### Check Logs

```bash
# Main application
docker compose -f docker-compose.production.yml logs -f icad_dispatch

# Public map
docker compose -f docker-compose.production.yml logs -f public_map
```

---

## Step 5: Access the Dashboard

1. Open your browser to `https://dispatch.yourdomain.com` (or `http://YOUR_SERVER_IP:9911`)
2. Log in with:
   - **Username:** `root`
   - **Password:** The `ROOT_PASSWORD` from your `.env` file
3. **Change the admin password immediately** via the user menu

---

## Step 6: Configure Your System

### Add Your Radio System

1. Go to **Dashboard → Systems**
2. Click **Add System**
3. Fill in:
   - System Name (e.g., "County Fire")
   - Decimal ID (your radio system ID)
   - Timezone
4. Save and note the **API Key** for uploading calls

### Enable Address Extraction

1. Go to **Dashboard → Systems → Your System → Address Extraction**
2. Enable **Address Extraction**
3. Configure **Geocoding Regions** (add your county/state)
4. Save

### Set Up Notifiers

1. Go to **Dashboard → Systems → Your System → Notifiers**
2. Configure your preferred channels:
   - [Discord](configuration/notifiers/discord.md)
   - [Telegram](configuration/notifiers/telegram.md)
   - [Email](configuration/notifiers/email.md)
   - [Pushover](configuration/notifiers/pushover.md)
   - [n8n](configuration/notifiers/n8n.md)
   - [Make](configuration/notifiers/make.md)
   - [Ntfy](configuration/notifiers/ntfy.md)

---

## Step 7: Test Upload

### Using the Dashboard Test Button

1. Go to **Dashboard → Systems → Your System**
2. Scroll to the notifier section
3. Click **Test** on any notifier (Discord, Telegram, etc.)
4. Verify the test call appears on the public map

### Using the API

```bash
curl -X POST https://dispatch.yourdomain.com/api/call-upload \
  -H "X-API-Key: YOUR_SYSTEM_API_KEY" \
  -F "audio=@/path/to/test.mp3" \
  -F "talkgroup=410837" \
  -F "system_id=1"
```

---

## Step 8: Set Up HTTPS (Production)

For production, use a reverse proxy with HTTPS:

### Option A: nginx

```nginx
server {
    listen 443 ssl http2;
    server_name dispatch.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:9911;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl http2;
    server_name map.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Option B: Caddy (Easiest)

```
dispatch.yourdomain.com {
    reverse_proxy localhost:9911
}

map.yourdomain.com {
    reverse_proxy localhost:5000
}
```

---

## Next Steps

- **[Security Hardening](security.md)** — Essential production checklist
- **[Geocoding Setup](configuration/geocoding.md)** — Configure address lookup
- **[Public Map](configuration/public-map.md)** — Customize the live map
- **[API Reference](api.md)** — Integrate with external systems
- **[Troubleshooting](troubleshooting.md)** — Fix common issues

---

## One-Click Alternative

Prefer automation? Use the [one-click installer](installation/one-click.md):

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/main/install.sh | bash
```
