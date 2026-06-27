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

## What This Is

The `install.sh` script does everything for you. You run one command, answer a few questions, and iCAD Dispatch is running.

**What it does:**
1. Checks that Docker and Git are installed
2. Downloads the code from GitHub
3. Asks you a few questions (domain, timezone, passwords)
4. Generates secure random passwords automatically
5. Creates the `.env` file
6. Builds and starts all 3 Docker containers
7. Prints a summary with your URLs and login info

---

## Requirements

- Fresh Ubuntu 22.04+ or Debian 12 server
- Root or sudo access
- Public IP address
- Domain name (recommended but not required)
- Internet connection

---

## Run the Installer

### Option 1: Direct Download (Easiest)

```bash
curl -fsSL https://raw.githubusercontent.com/renfrewcountyscanner/icad_dispatch_v2/main/install.sh | bash
```

**What this does:** Downloads the script and runs it immediately.

---

### Option 2: Download First, Review, Then Run

If you want to see what the script does before running it:

```bash
curl -fsSL https://raw.githubusercontent.com/renfrewcountyscanner/icad_dispatch_v2/main/install.sh -o install.sh
nano install.sh      # Read through the script
bash install.sh
```

---

## What the Script Asks You

| Question | What It Means | Example Answer |
|---|---|---|---|
| Dispatch domain | Your main dashboard URL | `dispatch.yourdomain.com` |
| Map domain | Your public map URL | `map.dispatch.yourdomain.com` |
| Timezone | Your region's IANA timezone | `America/New_York` (press Enter) |
| Use HTTPS? | Whether you have SSL certificates | `yes` or `no` |
| PostgreSQL password | Database password (or Enter to auto-generate) | Type a strong password or press Enter |
| Enable Google Maps? | Optional geocoding fallback | `yes` or `no` (default) |
| Enable OpenAI? | Optional LLM address extraction | `yes` or `no` (default) |

**The script auto-generates:**
- `ROOT_PASSWORD` — admin login password (shown in summary)
- `PUBLIC_MAP_API_KEY` — shared secret for the containers
- `MAP_SECRET_KEY` — secret for the public map
- `PG_PASSWORD` — database password (if you pressed Enter)

---

## What Happens After the Script Finishes

You'll see output like this:
```
============================================
iCAD Dispatch v2.5 is running!
============================================

Access at: http://192.168.1.50:9911
Log in with: root / your-password-here

Change your password after first login!
```

---

## Next Steps After Installation

### 1. Set Up HTTPS (Required)

The one-click installer does NOT set up HTTPS. You must do this separately.

#### Caddy (Easiest)

```bash
sudo apt-get install caddy
sudo nano /etc/caddy/Caddyfile
```

Paste this (replace with your domain):
```
dispatch.yourdomain.com {
    reverse_proxy localhost:9911
}

map.yourdomain.com {
    reverse_proxy localhost:5000
}
```

Reload:
```bash
sudo systemctl reload caddy
```

**Caddy automatically gets HTTPS certificates.**

---

### 2. Log In

1. Open `https://dispatch.yourdomain.com` (or `http://YOUR_IP:9911` if no domain)
2. Login:
   - **Username:** `root`
   - **Password:** The admin password you entered during installation
3. **Change the password immediately**

---

### 3. Configure Your System

Follow the [Quick Start Guide](../quickstart.md#step-7-configure-your-system) to:
- Add your radio system
- Enable address extraction
- Set up notifiers
- Test upload

---

## Troubleshooting

### "Docker is not installed"

Install Docker:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

---

### "Git clone failed"

Check internet connectivity:
```bash
curl -I https://github.com
```

If that works, try cloning manually:
```bash
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git /opt/icad_dispatch
```

---

### "Port 9911 already in use"

Find what's using the port:
```bash
sudo lsof -i :9911
```

Kill it or change the port during installation.

---

## Security Note

The installer:
- Generates random secrets automatically
- Sets `.env` file permissions to `600` (only owner can read)
- Does NOT set up HTTPS — you must do this manually
- Does NOT configure a firewall — see [Security Hardening](../security.md)

**After installation:**
1. Change the admin password immediately
2. Set up HTTPS (see above)
3. Enable firewall:
   ```bash
   sudo ufw default deny incoming
   sudo ufw allow ssh
   sudo ufw allow http
   sudo ufw allow https
   sudo ufw enable
   ```

---

*For manual setup, see [Docker Installation](docker.md) or [Native Installation](native.md).*
