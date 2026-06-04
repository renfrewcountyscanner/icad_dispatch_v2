---
layout: default
title: Quick Start
nav_order: 2
---

# Quick Start
{: .no_toc }

Get iCAD Dispatch v2 running in **15 minutes**.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Before You Start (Prerequisites)

You need:

1. A **Linux server** (Ubuntu 22.04+ recommended) with a public IP address
2. **Docker** and **Docker Compose** installed
3. A **domain name** pointing to your server (two subdomains recommended):
   - `dispatch.yourdomain.com` — for the admin dashboard
   - `map.yourdomain.com` — for the public live map
4. Root or sudo access to the server
5. Ability to copy/paste commands into a terminal

### Check If Docker Is Installed

```bash
docker --version
docker compose version
```

If you see version numbers, you're good. If you see "command not found", install Docker:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in for the group change to take effect
```

---

## Step 1: Download iCAD Dispatch

This downloads the app code from GitHub to your server.

```bash
cd ~
git clone https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git
cd icad_dispatch_v2
```

**What this does:** Creates a folder called `icad_dispatch_v2` with all the code, configs, and documentation.

---

## Step 2: Create Your Settings File

iCAD Dispatch needs a file called `.env` that contains all your passwords, URLs, and settings. We provide a template called `.env.example`.

```bash
cp .env.example .env
nano .env
```

**What this does:** Copies the template to a new file called `.env`, then opens it in the `nano` text editor.

### Values You MUST Change

These 6 values are **required**. The app will not start without them.

| Variable | What it is | Example |
|---|---|---|
| `BASE_URL` | Your dashboard URL | `https://dispatch.yourdomain.com` |
| `TIMEZONE` | Your timezone (find yours [here](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) | `America/New_York` |
| `PG_PASSWORD` | Database password | `MyVerySecretPassword123!` |
| `PUBLIC_MAP_API_KEY` | Shared secret between main app and public map | Generate with `openssl rand -hex 24` |
| `MAP_SECRET_KEY` | Secret for public map sessions | Generate with `openssl rand -hex 32` |
| `ROOT_PASSWORD` | Your admin login password | `MyAdminPassword456!` |

### How to Generate Random Secrets

Run these commands on your server and copy the output into `.env`:

```bash
# For PUBLIC_MAP_API_KEY (48 characters)
openssl rand -hex 24

# For MAP_SECRET_KEY (64 characters)
openssl rand -hex 32
```

### What Is PUBLIC_MAP_API_KEY?

This is a **shared password** between two containers:
- The main app (`icad_dispatch`) uses it to authenticate when pushing calls to the public map
- The public map (`public_map`) uses it to verify those calls are legitimate

**Both containers must have the exact same value.** If they don't match, the public map won't show new calls.

### Save and Exit nano

1. Press `Ctrl + O` (that's the letter O, not zero) to save
2. Press `Enter` to confirm the filename
3. Press `Ctrl + X` to exit

---

## Step 3: Start Everything

This single command downloads, builds, and starts all three containers:

```bash
docker compose -f docker-compose.production.yml up -d
```

**What this does:**
1. Downloads PostgreSQL 16 + PostGIS (the database)
2. Builds the main app container from the code
3. Builds the public map container from the code
4. Starts all three containers in the background (`-d` means "detached")

**How long it takes:**
- First run: **5–10 minutes** (downloads and builds everything)
- After that: **30 seconds**

---

## Step 4: Check That Everything Is Running

```bash
docker compose -f docker-compose.production.yml ps
```

**Expected output:**
```
NAME                              STATUS          PORTS
icad_dispatch_v2-postgres-1       Up (healthy)    5432/tcp
icad_dispatch_v2-icad_dispatch-1  Up (healthy)    0.0.0.0:9911->9911/tcp
icad_dispatch_v2-public_map-1     Up              0.0.0.0:5000->5000/tcp
```

**What each container does:**
- `postgres` — Stores all call data, transcripts, and settings
- `icad_dispatch` — The main app you log into (dashboard + API)
- `public_map` — The public live map citizens view

### If a Container Says "Exited" or "Restarting"

Check the logs to see what went wrong:

```bash
# Main app logs
docker compose -f docker-compose.production.yml logs -f icad_dispatch

# Public map logs
docker compose -f docker-compose.production.yml logs -f public_map

# Database logs
docker compose -f docker-compose.production.yml logs -f postgres
```

**Common fix:** If the main app says "Database connection refused", the database wasn't ready yet. Just wait 30 seconds and run `docker compose -f docker-compose.production.yml restart icad_dispatch`.

---

## Step 5: Set Up HTTPS (Required)

**You cannot use HTTP in production.** Browsers block audio playback and WebSocket connections over HTTP. You **must** use HTTPS.

You need a **reverse proxy** — a program that sits in front of your containers and handles HTTPS for you.

### Option A: Caddy (Easiest — Automatic HTTPS)

Caddy is the easiest option because it automatically gets HTTPS certificates from Let's Encrypt.

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

Save and reload:
```bash
sudo systemctl reload caddy
```

**What this does:**
- When someone visits `https://dispatch.yourdomain.com`, Caddy forwards them to the main app on port 9911
- When someone visits `https://map.yourdomain.com`, Caddy forwards them to the public map on port 5000
- Caddy automatically gets and renews HTTPS certificates

### Option B: nginx (More Control)

If you already use nginx or need more control:

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

**If you don't have SSL certificates yet**, use Let's Encrypt with Certbot:
```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d dispatch.yourdomain.com -d map.yourdomain.com
```

---

## Step 6: Log In and Change Your Password

1. Open `https://dispatch.yourdomain.com` in your browser
2. Login with:
   - **Username:** `root`
   - **Password:** The `ROOT_PASSWORD` from your `.env` file
3. **Immediately change the password** via the user menu (top right)

---

## Step 7: Configure Your Radio System

Now that you're logged in, you need to tell iCAD Dispatch about your radio system.

### Add Your Radio System

1. Go to **Dashboard → Systems**
2. Click **Add System**
3. Fill in:
   - **System Name:** e.g., "County Fire" or "EMS Dispatch"
   - **Decimal ID:** Your radio system ID (ask your radio tech if unsure)
   - **Timezone:** Select from the dropdown
4. Click **Save**
5. **Copy the API Key** shown on the system card — you'll need this to upload calls

### Enable Address Extraction

1. Click **Edit** on your new system
2. Go to the **Address Extraction** tab
3. Check **Enable Address Extraction**
4. Under **Geocoding Regions**, add your county and state
   - Example: County = `Renfrew`, State = `ON`, Country = `CA`
5. Click **Save**

**What this does:** When a call is uploaded, iCAD will try to find addresses in the transcript and look them up on a map.

### Set Up Notifiers

1. Click **Edit** on your system
2. Go to the **Notifiers** tab
3. Configure the channels you want:
   - [Discord](configuration/notifiers/discord.md)
   - [Telegram](configuration/notifiers/telegram.md)
   - [Email](configuration/notifiers/email.md)
   - [Pushover](configuration/notifiers/pushover.md)
   - [n8n](configuration/notifiers/n8n.md)
   - [Make](configuration/notifiers/make.md)
   - [Ntfy](configuration/notifiers/ntfy.md)

Each notifier has a **Test** button. Click it to send a test message and verify it works.

---

## Step 8: Test Upload

### Using the Dashboard Test Button

1. Go to **Dashboard → Systems → Your System**
2. Scroll to the notifier section
3. Click **Test** on any notifier (Discord, Telegram, etc.)
4. Check that the test call appears on `https://map.yourdomain.com`

### Using the API (Command Line)

```bash
curl -X POST https://dispatch.yourdomain.com/api/call-upload \
  -H "X-API-Key: YOUR_SYSTEM_API_KEY" \
  -F "audio=@/path/to/test.mp3" \
  -F "talkgroup=410837" \
  -F "system_id=1"
```

Replace:
- `YOUR_SYSTEM_API_KEY` — the key you copied in Step 7
- `/path/to/test.mp3` — path to an audio file on your server
- `410837` — your talkgroup ID
- `1` — your system ID (the number shown in the dashboard)

---

## Step 9: Security Hardening (Do This Next)

Before going live, secure your server:

### 1. Firewall

```bash
sudo ufw default deny incoming
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw enable
```

**What this does:** Blocks all incoming connections except SSH (for you to log in), HTTP, and HTTPS.

### 2. Never Expose Docker Ports Directly

The containers expose ports `9911` and `5000` on `0.0.0.0` by default. **Only your reverse proxy (Caddy/nginx) should access these.**

If you have a firewall, block ports 9911 and 5000 from the internet:
```bash
sudo ufw deny 9911
sudo ufw deny 5000
sudo ufw deny 5432
```

### 3. Read the Full Security Guide

See [Security Hardening](security.md) for:
- fail2ban setup
- Automatic security updates
- Database backups
- More hardening steps

---

## Common Commands You'll Use

```bash
# View all running containers
docker compose -f docker-compose.production.yml ps

# View logs (press Ctrl+C to stop watching)
docker compose -f docker-compose.production.yml logs -f icad_dispatch
docker compose -f docker-compose.production.yml logs -f public_map
docker compose -f docker-compose.production.yml logs -f postgres

# Restart a specific container
docker compose -f docker-compose.production.yml restart icad_dispatch
docker compose -f docker-compose.production.yml restart public_map

# Stop everything (keeps data)
docker compose -f docker-compose.production.yml down

# Start everything again
docker compose -f docker-compose.production.yml up -d

# Update to latest code
git pull origin main
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d

# Backup database
docker exec icad_dispatch_v2-postgres-1 pg_dump -U icad icad_dispatch > backup.sql

# Restore database
cat backup.sql | docker exec -i icad_dispatch_v2-postgres-1 psql -U icad -d icad_dispatch
```

---

## Next Steps

- **[Security Hardening](security.md)** — Essential production checklist
- **[Geocoding Setup](configuration/geocoding.md)** — Fine-tune address lookup
- **[Public Map](configuration/public-map.md)** — Customize the live map
- **[API Reference](api.md)** — Integrate with external systems
- **[Troubleshooting](troubleshooting.md)** — Fix common issues

---

## One-Click Alternative

Prefer automation? Use the [one-click installer](installation/one-click.md):

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/main/install.sh | bash
```
