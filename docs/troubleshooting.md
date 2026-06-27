---
layout: default
title: Troubleshooting
nav_order: 9
---

# Troubleshooting
{: .no_toc }

Common issues and their solutions.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Installation Issues

### "Docker command not found"

**Cause:** Docker is not installed.

**Fix:**
```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in

# Verify
docker --version
docker compose version
```

---

### "Port 9911 already in use"

**Cause:** Another service is using port 9911.

**Fix:**
```bash
# Find the process
sudo lsof -i :9911

# Kill it (if safe)
sudo kill -9 <PID>

# Or change the port in docker-compose.production.yml
# Change: "9911:9911" to "9912:9911"
```

---

### "Build fails with no space left on device"

**Cause:** Docker disk is full.

**Fix:**
```bash
# Clean up Docker
docker system prune -a

# Check disk space
df -h

# If still full, expand disk or move Docker data directory
```

---

## Startup Issues

### "Database connection refused"

**Cause:** PostgreSQL is not ready or credentials are wrong.

**Fix:**
```bash
# Check postgres is running
docker compose -f docker-compose.production.yml ps

# Check postgres logs
docker compose -f docker-compose.production.yml logs postgres

# Verify credentials match between .env and docker-compose
```

---

### "Container keeps restarting"

**Cause:** Application crash or health check failure.

**Fix:**
```bash
# Check logs
docker compose -f docker-compose.production.yml logs -f icad_dispatch

# Common causes:
# - Missing .env file
# - Invalid database credentials
# - Port conflict
# - Permission issues on volumes
```

---

### "Log directory is not writable" or permission denied

**Cause:** The container runs as user `UID 9911`, but the host directories are owned by a different user.

**Fix:**
```bash
# Create the user and group if they don't exist
sudo groupadd -g 9911 icad_dispatch 2>/dev/null || true
sudo useradd -M -s /usr/sbin/nologin -u 9911 -g icad_dispatch icad_dispatch 2>/dev/null || true

# Fix ownership of the mounted directories
sudo chown -R 9911:9911 var log audio

# Restart the container
docker compose -f docker-compose.production.yml restart icad_dispatch
```

---

## Dashboard Issues

### "Can't log in — invalid credentials"

**Cause:** Wrong password or account doesn't exist.

**Fix:**
```bash
# Reset root password by editing .env
nano .env
# Change ROOT_PASSWORD
# Restart container
docker compose -f docker-compose.production.yml restart icad_dispatch
```

### "404 Not Found on /admin/"

**Cause:** URL path issue.

**Fix:** Access `/admin/` (with trailing slash).

---

## Call Processing Issues

### "Calls not being processed"

**Cause:** API key missing or wrong.

**Fix:**
```bash
# Check system API key in dashboard
curl -X POST https://dispatch.yourdomain.com/api/call-upload \
  -H "X-API-Key: WRONG_KEY" \
  -F "audio@test.mp3" -F "talkgroup=1"
# Should return 401
```

### "Transcription not working"

**Cause:** Whisper model not downloaded or misconfigured.

**Fix:**
```bash
# Check logs for Whisper errors
docker compose -f docker-compose.production.yml logs -f icad_dispatch | grep -i whisper

# Verify Whisper model in settings
# Dashboard → Systems → Your System → Transcription
```

### "Address not extracted"

**Cause:** Address extraction disabled or LLM not configured.

**Fix:**
1. Go to **Dashboard → Systems → Your System → Address Extraction**
2. Ensure **Enable Address Extraction** is toggled on
3. Check transcript quality — garbled audio = poor extraction
4. Add your counties to the geocoding whitelist

---

## Notification Issues

### "No notifications sent"

**Cause:** Notifications muted or notifier not configured.

**Fix:**
1. Check **Dashboard → Systems → Your System** — is **Mute Notifications** on?
2. Toggle it off
3. Verify notifier is enabled and configured
4. Check logs: `docker compose logs -f icad_dispatch | grep -i "discord\|telegram\|email"`

### "Discord webhook returned 404"

**Cause:** Invalid webhook URL.

**Fix:**
1. In Discord, go to Channel Settings → Integrations → Webhooks
2. Verify the URL matches what's in iCAD
3. Re-create the webhook if necessary

### "Telegram bot not sending"

**Cause:** Bot not admin in channel or wrong channel ID.

**Fix:**
1. Add the bot as an administrator in the Telegram channel
2. Verify channel ID format: `-1001234567890` (include `-100`)
3. Test via @BotFather → /getchat → forward a message

---

## Public Map Issues

### "Map not updating in real-time"

**Cause:** WebSocket connection blocked or API key mismatch.

**Fix:**
```bash
# Check public map logs
docker compose -f docker-compose.production.yml logs -f public_map

# Verify PUBLIC_MAP_API_KEY matches in both containers
grep PUBLIC_MAP_API_KEY .env
grep PUBLIC_MAP_API_KEY docker-compose.production.yml

# Check browser console for WebSocket errors
# Ensure reverse proxy passes Upgrade headers
```

### "No calls showing on map"

**Cause:** Calls lack geocoded coordinates.

**Fix:**
1. Check call has lat/lng in dashboard
2. Verify geocoding is configured
3. Check incident type filters on the map
4. Verify time range includes the call

---

## Database Issues

### "Migration failed"

**Cause:** Database schema conflict.

**Fix:**
```bash
# Check migration logs
docker compose -f docker-compose.production.yml logs icad_dispatch | grep -i migration

# Manual migration check
docker exec -it icad_dispatch_v2-postgres-1 psql -U icad -d icad_dispatch
SELECT * FROM schema_migrations ORDER BY version DESC LIMIT 5;
```

### "Disk space full"

**Cause:** Audio files or logs consuming disk.

**Fix:**
```bash
# Check disk usage
du -sh audio/ log/ var/

# Clean old logs
find log/ -name "*.log-*" -mtime +7 -delete

# Archive old audio
# (Implement your own retention policy)
```

---

## Performance Issues

### "Dashboard is slow"

**Cause:** Too many calls loaded or database not indexed.

**Fix:**
```bash
# Check database size
docker exec icad_dispatch_v2-postgres-1 psql -U icad -d icad_dispatch -c "SELECT pg_size_pretty(pg_database_size('icad_dispatch'));"

# Common slow queries
docker exec icad_dispatch_v2-postgres-1 psql -U icad -d icad_dispatch -c "SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 5;"
```

### "Map is sluggish"

**Cause:** Too many markers or large dataset.

**Fix:**
- Reduce default time range in map settings
- Enable clustering (requires code change)
- Increase server resources (RAM/CPU)

---

## Getting Help

If none of these solutions work:

1. **Check logs**: `docker compose -f docker-compose.production.yml logs -f icad_dispatch`
2. **Search issues**: [GitHub Issues](https://github.com/renfrewcountyscanner/icad_dispatch_v2/issues)
3. **Start a discussion**: [GitHub Discussions](https://github.com/renfrewcountyscanner/icad_dispatch_v2/discussions)
4. **Include in your report**:
   - iCAD version (from dashboard footer)
   - Docker version (`docker --version`)
   - Relevant log excerpts
   - Steps to reproduce

---

*For setup instructions, see [Quick Start](quickstart.md).*
