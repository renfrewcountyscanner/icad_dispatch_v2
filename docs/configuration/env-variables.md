---
layout: default
title: Environment Variables
parent: Configuration
nav_order: 1
---

# Environment Variables
{: .no_toc }

Complete reference for `.env` configuration.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Required Variables

These must be set before starting iCAD Dispatch for the first time.

### `BASE_URL`

The public URL where your iCAD dashboard is accessible.

```
BASE_URL=https://dispatch.yourdomain.com
```

- Must include protocol (`http://` or `https://`)
- Used for generating absolute URLs in notifications and emails
- Must match your reverse proxy or direct access URL

### `TIMEZONE`

IANA timezone name for your dispatch region.

```
TIMEZONE=America/New_York
```

Common North American timezones:
- `America/New_York` — Eastern
- `America/Chicago` — Central
- `America/Denver` — Mountain
- `America/Los_Angeles` — Pacific
- `America/Anchorage` — Alaska
- `America/Halifax` — Atlantic

Find yours at [Wikipedia: List of tz database time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

### `PG_PASSWORD`

PostgreSQL database password.

```
PG_PASSWORD=change-me-to-a-strong-password
```

- Minimum 16 characters recommended
- Must match in both `.env` and `docker-compose.production.yml`
- Used by both `icad_dispatch` and `public_map` containers

### `PUBLIC_MAP_API_KEY`

Shared secret between iCAD dispatch and the public map.

```
PUBLIC_MAP_API_KEY=change-me-to-a-long-random-api-key
```

- Must be identical in both containers' environment
- Used to authenticate push calls from iCAD to public_map
- Generate with: `openssl rand -hex 24`

### `MAP_SECRET_KEY`

Flask secret key for the public map's SocketIO sessions.

```
MAP_SECRET_KEY=change-me-to-a-long-random-string-for-public-map
```

- Must be a long random string (64+ hex chars recommended)
- Different from `PUBLIC_MAP_API_KEY`
- Used for signing WebSocket session cookies
- Generate with: `openssl rand -hex 32`

### `ROOT_PASSWORD`

Password for the initial admin account.

```
ROOT_PASSWORD=change-me-before-first-boot
```

- Created on first database initialization
- Must be changed immediately after first login
- You can also use `ROOT_PASSWORD_FILE=/run/secrets/root_password`

---

## Cookie Settings

### `SESSION_COOKIE_SECURE`

Whether session cookies require HTTPS.

```
SESSION_COOKIE_SECURE=True
```

- Set `True` when using HTTPS in production
- Set `False` for local development or HTTP-only deployments

### `SESSION_COOKIE_DOMAIN`

Domain scope for session cookies.

```
SESSION_COOKIE_DOMAIN=yourdomain.com
```

- Should match the domain portion of `BASE_URL`
- Do not include protocol or path
- Example: if `BASE_URL=https://dispatch.yourdomain.com`, use `yourdomain.com`

### `SESSION_COOKIE_NAME`

Name of the session cookie.

```
SESSION_COOKIE_NAME=icad_dispatch
```

- Default is fine for most deployments
- Change if running multiple Flask apps on the same domain

### `SESSION_COOKIE_PATH`

Path scope for session cookies.

```
SESSION_COOKIE_PATH=/
```

- Default `/` is correct for most deployments

---

## Database Settings

### `PG_HOST`

PostgreSQL server hostname.

```
PG_HOST=postgres
```

- Use `postgres` when running in Docker Compose (service name)
- Use `localhost` for native installations

### `PG_PORT`

PostgreSQL server port.

```
PG_PORT=5432
```

- Default 5432 is standard
- Change only if your PostgreSQL runs on a non-standard port

### `PG_DATABASE`

Database name.

```
PG_DATABASE=icad_dispatch
```

### `PG_USER`

Database username.

```
PG_USER=icad
```

---

## Logging

### `LOG_LEVEL`

Verbosity of application logging.

```
LOG_LEVEL=2
```

| Level | Description |
|-------|-------------|
| 0 | Silent (no logs) |
| 1 | Errors only |
| 2 | Warnings + info (recommended) |
| 3 | Debug (very noisy, useful for troubleshooting) |

---

## Optional Variables

### `GOOGLE_MAPS_API_KEY`

Google Maps Geocoding API key for address fallback.

```
GOOGLE_MAPS_API_KEY=your-google-maps-api-key
```

- Optional — Nominatim (OpenStreetMap) is used by default
- Required only if you want Google as a geocoding fallback
- Get a key at [Google Cloud Console](https://developers.google.com/maps/documentation/geocoding/get-api-key)
- Costs may apply for high volume usage

### `OPENAI_API_KEY`

OpenAI API key for LLM-based address extraction.

```
OPENAI_API_KEY=sk-your-openai-key
```

- Optional — regex-based extraction works without this
- Enables more accurate address parsing from transcripts
- Required for incident classification if using GPT-4

### `WHISPER_MODEL`

Whisper model size for local transcription.

```
WHISPER_MODEL=base
```

| Model | Speed | Accuracy | VRAM Required |
|-------|-------|----------|---------------|
| `tiny` | Fastest | Basic | ~1 GB |
| `base` | Fast | Good | ~1 GB |
| `small` | Moderate | Better | ~2 GB |
| `medium` | Slow | Excellent | ~5 GB |
| `large` | Slowest | Best | ~10 GB |

### `WHISPER_API_KEY`

OpenAI API key for remote Whisper transcription.

```
WHISPER_API_KEY=sk-your-openai-key
```

- Use instead of local model if you have API credits
- Faster and more accurate than local models
- Requires internet connectivity

### `PUBLIC_MAP_BASE_URL`

Public-facing URL of the live map.

```
PUBLIC_MAP_BASE_URL=https://map.yourdomain.com
```

- Used for generating map image URLs in notifications
- Should match the `BASE_URL` in public_map's environment
- Set automatically by the one-click installer

---

## Docker-Compose Specific

These are set in `docker-compose.production.yml`, not `.env`:

### `PUBLIC_MAP_URL`

Internal URL for pushing calls to the public map.

```yaml
environment:
  - PUBLIC_MAP_URL=http://public_map:5000/api/push-call
```

- Uses Docker internal networking
- Do not change unless you modify service names

---

## Complete Example

```bash
# ─── Required ───
BASE_URL=https://dispatch.yourdomain.com
TIMEZONE=America/New_York
PG_PASSWORD=your-unique-strong-password-here
PUBLIC_MAP_API_KEY=change-me-to-a-long-random-api-key
MAP_SECRET_KEY=another-long-random-string-here
ROOT_PASSWORD=change-me-immediately

# ─── Cookies ───
SESSION_COOKIE_SECURE=True
SESSION_COOKIE_DOMAIN=yourdomain.com
SESSION_COOKIE_NAME=icad_dispatch
SESSION_COOKIE_PATH=/

# ─── Database ───
PG_HOST=postgres
PG_PORT=5432
PG_DATABASE=icad_dispatch
PG_USER=icad

# ─── Optional ───
LOG_LEVEL=2
GOOGLE_MAPS_API_KEY=your-google-maps-api-key
OPENAI_API_KEY=sk-your-openai-key
PUBLIC_MAP_BASE_URL=https://map.yourdomain.com
```

---

*For Docker-specific configuration, see [Docker Installation](../installation/docker.md).*
