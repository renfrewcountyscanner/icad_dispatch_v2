---
layout: default
title: Native Installation
parent: Installation
nav_order: 2
---

# Native Installation
{: .no_toc }

For advanced users who prefer not to use Docker.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

This guide installs iCAD Dispatch directly on your Linux server without containers.

**Not recommended for most users.** Docker provides easier updates, better isolation, and consistent environments.

---

## Prerequisites

- Ubuntu 22.04 LTS or Debian 12
- Root or sudo access
- Domain name (optional but recommended)

---

## Step 1: Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    python3.12 python3.12-venv python3-pip \
    postgresql-16 postgresql-16-postgis-3 \
    ffmpeg libavcodec-extra \
    git nginx certbot python3-certbot-nginx
```

---

## Step 2: Configure PostgreSQL

```bash
# Start PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Create database and user
sudo -u postgres psql <<EOF
CREATE USER icad WITH PASSWORD 'change-me-to-strong-password';
CREATE DATABASE icad_dispatch OWNER icad;
\c icad_dispatch
CREATE EXTENSION IF NOT EXISTS postgis;
GRANT ALL PRIVILEGES ON DATABASE icad_dispatch TO icad;
EOF
```

---

## Step 3: Create Application User

```bash
sudo useradd -r -s /bin/false -d /opt/icad_dispatch icad
sudo mkdir -p /opt/icad_dispatch
sudo chown icad:icad /opt/icad_dispatch
```

---

## Step 4: Clone and Install

```bash
sudo -u icad bash
cd /opt/icad_dispatch
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git .

python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 5: Configure Environment

```bash
cp .env.example .env
nano .env
```

Set at minimum:
- `BASE_URL`
- `TIMEZONE`
- `PG_PASSWORD`
- `PUBLIC_MAP_API_KEY`
- `MAP_SECRET_KEY`
- `ROOT_PASSWORD`

---

## Step 6: Create Directories

```bash
mkdir -p var/public_maps log audio
```

---

## Step 7: Run Migrations

```bash
source venv/bin/activate
python3 <<EOF
from lib.postgres_module import PostgreSQLDatabase
db = PostgreSQLDatabase()
EOF
```

Migrations run automatically when the database wrapper is instantiated.

---

## Step 8: Create Systemd Service

```bash
sudo nano /etc/systemd/system/icad_dispatch.service
```

```ini
[Unit]
Description=iCAD Dispatch v2
After=network.target postgresql.service

[Service]
Type=simple
User=icad
Group=icad
WorkingDirectory=/opt/icad_dispatch
Environment="PATH=/opt/icad_dispatch/venv/bin"
ExecStart=/opt/icad_dispatch/venv/bin/gunicorn -w 2 -b 127.0.0.1:9911 --timeout 120 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable icad_dispatch
sudo systemctl start icad_dispatch
```

---

## Step 9: Install Public Map

The public map requires a separate Python environment:

```bash
cd /opt/icad_dispatch/public_map
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create systemd service:

```bash
sudo nano /etc/systemd/system/icad_public_map.service
```

```ini
[Unit]
Description=iCAD Public Map
After=network.target

[Service]
Type=simple
User=icad
Group=icad
WorkingDirectory=/opt/icad_dispatch/public_map
Environment="PATH=/opt/icad_dispatch/public_map/venv/bin"
Environment="PG_HOST=localhost"
Environment="PG_PORT=5432"
Environment="PG_DATABASE=icad_dispatch"
Environment="PG_USER=icad"
Environment="PG_PASSWORD=your-db-password"
Environment="BASE_URL=https://map.yourdomain.com"
Environment="SECRET_KEY=your-secret-key"
Environment="PUBLIC_MAP_API_KEY=your-api-key"
ExecStart=/opt/icad_dispatch/public_map/venv/bin/gunicorn --worker-class eventlet -w 1 -b 127.0.0.1:5000 --timeout 120 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable icad_public_map
sudo systemctl start icad_public_map
```

---

## Step 10: Configure nginx

```bash
sudo nano /etc/nginx/sites-available/icad
```

```nginx
server {
    listen 80;
    server_name dispatch.yourdomain.com map.yourdomain.com;
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

```bash
sudo ln -s /etc/nginx/sites-available/icad /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 11: HTTPS with Let's Encrypt

```bash
sudo certbot --nginx -d dispatch.yourdomain.com -d map.yourdomain.com
```

---

## Updating (Native)

```bash
sudo -u icad bash
cd /opt/icad_dispatch
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart icad_dispatch
```

---

## Troubleshooting

**Gunicorn fails to start**
```bash
sudo journalctl -u icad_dispatch -n 50
```

**PostgreSQL connection refused**
```bash
sudo systemctl status postgresql
sudo -u postgres psql -c "\l"
```

**Permission denied on audio files**
```bash
sudo chown -R icad:icad /opt/icad_dispatch/audio
```

---

*For most users, [Docker Installation](docker.md) is strongly recommended.*
