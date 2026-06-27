---
layout: default
title: Security
nav_order: 7
---

# Security Hardening
{: .no_toc }

Essential checklist for production deployments.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Pre-Deployment Checklist

### Secrets

- [ ] Changed all default passwords (`ROOT_PASSWORD`, `PG_PASSWORD`)
- [ ] Generated strong `PUBLIC_MAP_API_KEY` (`openssl rand -hex 24`)
- [ ] Generated strong `MAP_SECRET_KEY` (`openssl rand -hex 32`)
- [ ] `.env` file has permissions `600` (owner read/write only)
- [ ] No secrets committed to Git

### HTTPS

- [ ] Using HTTPS in production (`BASE_URL` starts with `https://`)
- [ ] `SESSION_COOKIE_SECURE=True` when using HTTPS
- [ ] Valid SSL certificate (Let's Encrypt, commercial, or self-signed)
- [ ] HTTP redirects to HTTPS

### Firewall

- [ ] Only ports 22 (SSH), 80 (HTTP), 443 (HTTPS) open to the internet
- [ ] Port 9911 (iCAD) blocked from external access
- [ ] Port 5000 (public_map) blocked from external access
- [ ] Port 5432 (PostgreSQL) blocked from external access
- [ ] UFW or iptables enabled

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw enable
```

### Reverse Proxy

- [ ] nginx or Caddy configured as reverse proxy
- [ ] WebSocket upgrade headers configured for public map
- [ ] Rate limiting enabled at proxy level (optional but recommended)

### Authentication

- [ ] Changed default `root` password after first login
- [ ] Created non-admin user accounts for daily use
- [ ] Admin access restricted to trusted personnel

### Database

- [ ] PostgreSQL password is strong and unique
- [ ] PostgreSQL not exposed to the internet (bind to localhost or Docker internal)
- [ ] Regular database backups configured

```bash
# Daily backup via cron
crontab -e
# Add: 0 2 * * * docker exec icad_dispatch_v2-postgres-1 pg_dump -U icad icad_dispatch > /backup/icad_$(date +\%Y\%m\%d).sql
```

### Container Security

- [ ] Running as non-root user (if supported by your environment)
- [ ] Docker daemon secured (if multi-user server)
- [ ] Regular image updates: `docker compose pull && docker compose up -d`

---

## Authentication & Authorization

### User Roles

| Role | Permissions |
|------|-------------|
| **Admin** | Full access: systems, triggers, users, corrections, settings |
| **User** | View-only: dashboard, call history, map corrections for assigned systems |

### API Keys

- System API keys are used for call uploads
- Each radio system has its own key
- Rotate keys periodically via dashboard

### Session Security

- Sessions stored in filesystem (`./var/sessions/`)
- CSRF tokens required for all state-changing operations
- Session timeout: configurable via Flask settings

---

## Data Privacy

### Public Map

The public map displays the same information broadcast over public emergency radio frequencies. No sensitive personal data is included.

What is public:
- Incident type and location
- Transcript of radio traffic
- Audio recording (if captured)

What is NOT public:
- Caller names or phone numbers
- Medical details beyond incident type
- Internal dispatch notes

### Location Accuracy

- Automatic geocoding is approximate
- Addresses from radio traffic may be unclear or incomplete
- Manual corrections can improve accuracy via dashboard

---

## Incident Response

### If You Suspect Unauthorized Access

1. Check logs: `docker compose logs -f icad_dispatch`
2. Review active sessions in dashboard
3. Rotate API keys for all systems
4. Change admin passwords
5. Check PostgreSQL access logs
6. Review firewall rules

### Reporting Security Issues

See [SECURITY.md](https://github.com/renfrewcountyscanner/icad_dispatch_v2/blob/main/SECURITY.md) for responsible disclosure.

---

## Advanced Hardening

### Fail2Ban

Protect against brute force login attempts:

```bash
sudo apt-get install fail2ban
sudo nano /etc/fail2ban/jail.local
```

```ini
[icad_dispatch]
enabled = true
port = http,https
filter = icad_dispatch
logpath = /path/to/icad_dispatch/log/icad_dispatch.log
maxretry = 5
bantime = 3600
```

### Docker Secrets (Advanced)

For production deployments, use Docker secrets instead of `.env`:

```yaml
secrets:
  db_password:
    external: true
  map_secret_key:
    external: true

services:
  icad_dispatch:
    secrets:
      - db_password
      - map_secret_key
```

### Separate Database User for Public Map

Create a read-only user for the public map:

```sql
CREATE USER icad_read WITH PASSWORD 'read-only-password';
GRANT CONNECT ON DATABASE icad_dispatch TO icad_read;
GRANT USAGE ON SCHEMA public TO icad_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO icad_read;
```

---

*For setup instructions, see [Quick Start](quickstart.md).*
