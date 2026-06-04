---
layout: default
title: Architecture
title: Architecture
nav_order: 3
---

# Architecture
{: .no_toc }

How iCAD Dispatch v2 works under the hood.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## The 3 Containers

iCAD Dispatch runs as **3 separate Docker containers**. Think of them as 3 separate programs that talk to each other over a private network.

### Container 1: `postgres` — The Database

**What it does:** Stores everything — calls, transcripts, addresses, user accounts, system settings.

**Why you need it:** Without the database, the other two containers have nowhere to save or read data.

**Technology:** PostgreSQL 16 with PostGIS extension (for storing map coordinates)

**Port:** `5432` (only accessible inside Docker, never to the internet)

**Data persistence:** Uses a Docker volume called `postgres_data`. Even if you delete and recreate the container, your data survives.

**Special behavior:**
- Must start first and report "healthy" before the other containers start
- Only the other two containers need to talk to it
- **Never expose this port to the internet**

---

### Container 2: `icad_dispatch` — The Brain

**What it does:** This is the main application. It:
- Accepts uploaded radio audio via `/api/call-upload`
- Detects paging tones (two-tone, long-tone, MDC, DTMF)
- Transcribes speech using OpenAI Whisper
- Extracts addresses from transcripts
- Classifies incidents (Fire, Medical, Traffic, etc.)
- Sends notifications to Discord, Telegram, Email, etc.
- Serves the admin dashboard where you configure everything

**Why you need it:** This is what you log into. It does all the processing.

**Technology:** Python 3.12 + Flask

**Port:** `9911`

**Special behavior:**
- Must be behind a reverse proxy (nginx/Caddy) with HTTPS in production
- Reads environment variables from `.env`
- Waits for `postgres` to be healthy before starting
- Pushes new calls to `public_map` via HTTP

---

### Container 3: `public_map` — The Public Map

**What it does:** Shows a real-time map of emergency calls that anyone can view in their browser.

**Why you need it:** This is what citizens, news outlets, and the public see.

**Technology:** Python 3.12 + Flask + Socket.IO (for real-time updates)

**Port:** `5000`

**Special behavior:**
- **Completely separate** from the main app — it cannot modify anything
- Has read-only access to the database
- Uses **WebSocket** to push new calls to browsers instantly
- Needs the same `PUBLIC_MAP_API_KEY` as the main app (this is how the main app proves it's legitimate when pushing calls)
- Must also be behind a reverse proxy with HTTPS

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
                     All three share the same
                     Docker internal network
```

**Important details:**
- All three containers are on the same **Docker network**
- They can talk to each other by **container name**:
  - `icad_dispatch` connects to `postgres` by hostname `postgres`
  - `public_map` connects to `postgres` by hostname `postgres`
  - `icad_dispatch` pushes calls to `public_map` at `http://public_map:5000/api/push-call`
- The reverse proxy (Caddy/nginx) is **outside** Docker and forwards internet traffic to the containers

---

## Data Flow: From Radio to Public Map

### 1. Call Upload

A radio transmission (MP3/WAV) is uploaded to the `/api/call-upload` endpoint with:
- `audio` file
- `talkgroup` ID
- `system_id`

**Who does this:** Your radio scanner software, SDR receiver, or custom upload script.

---

### 2. Tone Detection

The audio is analyzed for paging tones:
- **Two-tone paging** (e.g., 800ms @ 1540Hz + 800ms @ 1740Hz)
- **Long-tone** (continuous tone for 3+ seconds)
- **MDC** (Motorola Digital Control)
- **DTMF** (touch tones)

Detected tones are matched against configured tone sets to determine which station was paged.

**Result:** A list of `alert_triggers` that fired.

---

### 3. Speech Transcription

The audio is transcribed using OpenAI Whisper:
- **Local mode:** Uses a local Whisper model (base/small/medium)
- **API mode:** Sends audio to OpenAI's Whisper API

**Result:** Full transcript text + per-word timestamps.

---

### 4. Address Extraction

The transcript is analyzed for location information:
- **LLM extraction** (optional): OpenAI GPT-4 extracts structured address components
- **Regex fallback:** Pattern matching for common address formats

**Result:** Street, city, county, state, postal code.

---

### 5. Geocoding

Extracted addresses are converted to map coordinates (lat/lng):
1. **Nominatim** (OpenStreetMap) — primary, free, rate-limited
2. **Google Maps** (optional) — fallback, requires API key

Validated against configured region whitelist (state + county).

**Result:** Latitude, longitude, and formatted address.

---

### 6. Incident Classification

The transcript is classified into incident types:
- Fire, Medical, Traffic, Rescue, Utilities, HazMat, Other

Uses LLM classification with confidence scores.

**Result:** Incident category + confidence percentage.

---

### 7. Database Storage

All data is saved to PostgreSQL:
- `call_records` — core call metadata (time, duration, talkgroup)
- `call_transcripts` — transcript text + extracted address
- `call_corrections` — manual location corrections (if any)
- `trigger_fires` — which tones triggered which alerts
- `radio_systems` — system configuration
- `alert_triggers` — tone set definitions

---

### 8. Notification Dispatch

A single notification context (`ctx`) is built containing:
- Call ID, time, duration
- System name, talkgroup
- Trigger names
- Transcript text
- Address + lat/lng + map URL
- Audio URL
- Incident category

This context is sent to **all enabled notifiers simultaneously**:
- Discord (embed with map image)
- Telegram (message with audio)
- Email (HTML body with map image)
- Pushover (mobile push notification)
- n8n (webhook to automation platform)
- Make (webhook to automation platform)
- Ntfy (mobile push via Ntfy server)

---

### 9. Public Map Update

The public map receives new calls via **two paths**:

**Path A: Push (real-time)**
- The main app sends an HTTP POST to `public_map/api/push-call`
- Includes the call data + `X-API-Key` header for authentication
- The public map immediately broadcasts to all connected browsers via WebSocket

**Path B: Poll (catch-up)**
- The public map polls PostgreSQL every 5 seconds for calls it hasn't seen
- This handles cases where the push failed or the public map was restarted

**Result:** Browsers see new calls instantly on the live map.

---

## Security Model

```
Internet
    │
    ▼
[Reverse Proxy]  ← HTTPS termination, rate limiting
    │
    ├──► / (iCAD Dashboard)  ← Auth required (login page)
    │
    └──► / (Public Map)      ← Read-only, no auth needed
         │
         └──► /api/push-call  ← API key required (only main app can push)
```

| Component | Authentication | Why |
|---|---|---|
| Dashboard | Session-based login | Only admins should configure the system |
| Public Map | None (intentional) | Public data — anyone can view |
| API Endpoints | `X-API-Key` header or session | Prevents unauthorized uploads |
| Push Endpoint | `PUBLIC_MAP_API_KEY` match | Ensures only the main app can push calls |

---

## Component Details

### iCAD Dispatch (Main Flask Application)

| Module | Purpose |
|--------|---------|
| `app.py` | Flask app factory, routes, error handlers |
| `lib/audio_file_handler.py` | Audio validation, conversion, normalization |
| `lib/tone_detection.py` | Tone detection via icad_tone_detection |
| `lib/transcribe_module.py` | Whisper transcription (local or API) |
| `lib/address_extractor_module.py` | Address extraction + geocoding |
| `lib/incident_classifier_module.py` | AI incident classification |
| `lib/dispatch_module.py` | Orchestrates notification dispatch |
| `lib/dispatch_text_render.py` | Template expansion for notifier messages |
| `lib/map_pin_renderer.py` | Static map image generation |
| `lib/postgres_module.py` | PostgreSQL database wrapper |

### Public Map (Separate Flask Application)

| Module | Purpose |
|--------|---------|
| `public_map/app.py` | Flask + SocketIO app |
| `public_map/static/js/map.js` | Frontend map logic (Leaflet) |
| `public_map/templates/map.html` | Map page template |

---

## Database Schema (Simplified)

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  radio_systems  │────►│  call_records   │◄────│  call_transcripts│
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                       │
         │              ┌────────┴────────┐
         │              │                 │
         ▼              ▼                 ▼
┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐
│ alert_triggers  │  │ trigger_fires │  │ call_corrections │
└─────────────────┘  └──────────────┘  └─────────────────┘
```

| Table | What It Stores |
|---|---|
| `radio_systems` | Your dispatch system configs (tone sets, upload settings) |
| `call_records` | Every radio call (time, duration, talkgroup, audio path) |
| `call_transcripts` | Transcript text + extracted address + geocoded location |
| `trigger_fires` | Which tones triggered which alerts for each call |
| `call_corrections` | Manual location corrections made via dashboard |
| `alert_triggers` | Tone definitions and notification settings |

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Python 3.12, Flask |
| **Database** | PostgreSQL 16, PostGIS |
| **Cache** | In-memory (flask-session with filesystem) |
| **Frontend** | Vanilla JS, Leaflet, Bootstrap |
| **Real-time** | Flask-SocketIO (eventlet) |
| **Audio** | pydub, webrtcvad, ffmpeg |
| **AI/ML** | OpenAI Whisper, GPT-4 |
| **Container** | Docker, Docker Compose |
| **Reverse Proxy** | nginx or Caddy |

---

## Scaling Considerations

For high-volume dispatch centers (100+ calls/day):

1. **Database:** Move PostgreSQL to dedicated server or managed service (RDS, Cloud SQL)
2. **Audio Storage:** Use S3-compatible object storage instead of local disk
3. **Transcription:** Use OpenAI Whisper API instead of local model for faster processing
4. **Map Tiles:** Self-host tile server or use commercial tile provider with higher rate limits
5. **Load Balancing:** Run multiple iCAD dispatch containers behind a load balancer

---

*For setup instructions, see the [Quick Start Guide](quickstart.md).*
