---
layout: default
title: Public Map
parent: Configuration
nav_order: 3
---

# Public Map Configuration
{: .no_toc }

Set up the live public emergency map.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

The public map is a **separate, read-only Flask application** that displays emergency calls in real-time. It connects to the same PostgreSQL database as iCAD dispatch but cannot modify any data.

**Key features:**
- Real-time updates via WebSocket (SocketIO)
- Dark/light mode toggle
- Time range filters (3h to 72h, or custom dates)
- System and trigger filters
- Incident type filters (Fire, Medical, Traffic, etc.)
- Audio playback
- Mobile-responsive design
- Public access — no login required

---

## Architecture

```
Radio ──► iCAD Dispatch ──► PostgreSQL ◄──► Public Map ◄──► Browser
                                        │                  │
                                        │                  │
                                        └── WebSocket ───────┘
```

The public map receives new calls via:
1. **Push** — iCAD calls `/api/push-call` on the public map via HTTP
2. **Poll** — public_map polls PostgreSQL every 5 seconds as catch-up

---

## Configuration

### Environment Variables

Set in `docker-compose.production.yml` or `.env`:

| Variable | Description | Example |
|----------|-------------|---------|
| `BASE_URL` | Public-facing map URL | `https://map.yourdomain.com` |
| `SECRET_KEY` | Flask-SocketIO session signing | `openssl rand -hex 32` |
| `PUBLIC_MAP_API_KEY` | Shared secret with iCAD | Same as iCAD's `PUBLIC_MAP_API_KEY` |
| `PG_HOST` | PostgreSQL host | `postgres` (Docker) or `localhost` |
| `PG_PORT` | PostgreSQL port | `5432` |
| `PG_DATABASE` | Database name | `icad_dispatch` |
| `PG_USER` | Database user | `icad` |
| `PG_PASSWORD` | Database password | From `.env` |

### Reverse Proxy

The public map should be served via HTTPS:

**Caddy:**
```
map.yourdomain.com {
    reverse_proxy localhost:5000
}
```

**nginx:**
```nginx
server {
    listen 443 ssl http2;
    server_name map.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**Note:** WebSocket requires `Upgrade` and `Connection` headers for real-time updates.

---

## Customization

### Default Map Center

Edit `public_map/static/js/map.js`:

```javascript
const RENFREW_CENTER = [45.4748, -77.6972];  // Change to your location
```

Change to your dispatch center coordinates:
```javascript
const DEFAULT_CENTER = [40.7589, -73.9851];  // Example: NYC
```

### Default Zoom

```javascript
const DEFAULT_ZOOM = 10;  // 1 = world, 20 = building
```

### Tile Provider

By default, standard OSM tiles are used with CSS darkening. To use a different tile provider, edit `public_map/static/js/map.js`:

```javascript
function addDarkTiles() {
    document.getElementById('map').classList.add('dark-tiles');
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
        maxZoom: 20
    }).addTo(map);
}
```

Popular alternatives:
- **CartoDB Dark Matter** — `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png`
- **Stamen Terrain** — `https://stamen-tiles-{s}.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png`
- **Esri World Imagery** — `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`

---

## Map Images in Notifications

When iCAD generates map images for notifications, they are saved to `./var/public_maps/` and served by the public map container at:

```
https://map.yourdomain.com/static/public_maps/map1.png
```

The public map container must have read access to this directory:

```yaml
volumes:
  - ./var/public_maps:/app/static/public_maps:ro
```

---

## Security

### Rate Limiting

The public map has built-in rate limiting:
- 60 requests per minute per IP
- Applied to `/api/calls`, `/api/triggers`, `/api/calls/<id>`

### API Key

The `/api/push-call` endpoint requires the `X-API-Key` header to match `PUBLIC_MAP_API_KEY`. This prevents unauthorized push attempts.

### Read-Only

The public map database user should ideally have read-only privileges. Currently it uses the same credentials as iCAD dispatch. For enhanced security, create a separate read-only PostgreSQL user.

### Firewall

- Do **not** expose port 5000 directly to the internet
- Use a reverse proxy (nginx/Caddy) on ports 80/443
- Block port 5000 with firewall: `sudo ufw deny 5000`

---

## Troubleshooting

### "Map not updating"

- Check WebSocket connection (browser console)
- Verify `PUBLIC_MAP_API_KEY` matches between iCAD and public_map
- Check public_map logs for push errors

### "No markers on map"

- Calls need lat/lng to show markers
- Check geocoding is working
- Verify incident filters are enabled

### "Slow map loading"

- First load fetches up to 5000 calls
- Consider reducing the default time range
- Check PostgreSQL query performance

---

*For notifier configuration, see [Discord](notifiers/discord.md), [Telegram](notifiers/telegram.md), etc.*
