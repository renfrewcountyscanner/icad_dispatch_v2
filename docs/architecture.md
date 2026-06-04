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

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              RADIO SOURCES                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                     │
│  │ SDR Receiver │  │  Scanner App  │  │  Audio File  │                     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                     │
└─────────┼─────────────────┼─────────────────┼─────────────────────────────┘
          │                 │                 │
          └─────────────────┴─────────────────┘
                            │
                    ┌───────▼───────┐
                    │  iCAD Dispatch │
                    │   (Flask App)  │
                    └───────┬───────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
    ┌─────▼─────┐    ┌──────▼──────┐    ┌─────▼─────┐
    │  Tone     │    │  Whisper    │    │  Address  │
    │ Detection │    │Transcription│    │ Extraction│
    └─────┬─────┘    └──────┬──────┘    └─────┬─────┘
          │                 │                 │
          └─────────────────┴─────────────────┘
                            │
                    ┌───────▼───────┐
                    │   PostgreSQL   │
                    │  (PostGIS 16)  │
                    └───────┬───────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
    ┌─────▼─────┐    ┌──────▼──────┐    ┌─────▼─────┐
    │  Discord  │    │  Telegram   │    │   Email   │
    └───────────┘    └─────────────┘    └───────────┘
    ┌───────────┐    ┌─────────────┐    ┌───────────┐
    │  Pushover │    │    n8n      │    │   Make    │
    └───────────┘    └─────────────┘    └───────────┘
    ┌───────────┐
    │   Ntfy    │
    └───────────┘
                            │
                    ┌───────▼───────┐
                    │  Public Map    │
                    │ (Flask-SocketIO)│
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │    Browser     │
                    │  (WebSocket)   │
                    └───────────────┘
```

---

## Data Flow

### 1. Call Upload

A radio transmission (MP3/WAV) is uploaded to the `/api/call-upload` endpoint with:
- `audio` file
- `talkgroup` ID
- `system_id`

### 2. Tone Detection

The audio is analyzed for:
- **Two-tone paging** (e.g., 800ms @ 1540Hz + 800ms @ 1740Hz)
- **Long-tone** (continuous tone for 3+ seconds)
- **MDC** (Motorola Digital Control)
- **DTMF** (touch tones)

Detected tones are matched against configured tone sets to determine which triggers fired.

### 3. Speech Transcription

The audio is transcribed using OpenAI Whisper:
- **Local mode**: Uses a local Whisper model (base/small/medium)
- **API mode**: Sends audio to OpenAI's Whisper API

Output: Full transcript text + per-word timestamps.

### 4. Address Extraction

The transcript is analyzed for location information:
- **LLM extraction** (optional): OpenAI GPT-4 extracts structured address components
- **Regex fallback**: Pattern matching for common address formats

Output: Street, city, county, state, postal code.

### 5. Geocoding

Extracted addresses are converted to lat/lng:
1. **Nominatim** (OpenStreetMap) — primary, free, rate-limited
2. **Google Maps** (optional) — fallback, requires API key

Validated against configured region whitelist (state + county).

### 6. Incident Classification

The transcript is classified into incident types:
- Fire, Medical, Traffic, Rescue, Utilities, HazMat, Other

Uses LLM classification with confidence scores.

### 7. Database Storage

All data is persisted in PostgreSQL:
- `call_records` — core call metadata
- `call_transcripts` — transcript text + extracted address
- `call_corrections` — manual location corrections
- `trigger_fires` — which tones triggered which alerts
- `radio_systems` — system configuration
- `alert_triggers` — tone set definitions

### 8. Notification Dispatch

A single notification context (`ctx`) is built containing:
- Call metadata (ID, time, duration)
- System info (name, talkgroup)
- Trigger names
- Transcript text
- Address + lat/lng + map URL
- Audio URL
- Incident category

This context is sent to all enabled notifiers simultaneously.

### 9. Public Map Update

The public map receives new calls via two paths:
- **Push**: iCAD calls the public_map `/api/push-call` endpoint via HTTP
- **Poll**: public_map polls PostgreSQL every 5 seconds for catch-up

The map broadcasts to browsers via Flask-SocketIO WebSocket.

---

## Component Details

### iCAD Dispatch (Flask Application)

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
| `lib/map_image_service.py` | Static map image generation |
| `lib/postgres_module.py` | PostgreSQL database wrapper |

### Public Map (Separate Flask Application)

| Module | Purpose |
|--------|---------|
| `public_map/app.py` | Flask + SocketIO app |
| `public_map/static/js/map.js` | Frontend map logic |
| `public_map/templates/map.html` | Map page template |

---

## Security Model

```
Internet
    │
    ▼
[Reverse Proxy]  ← HTTPS termination, rate limiting
    │
    ├──► / (iCAD Dashboard)  ← Auth required
    │
    └──► / (Public Map)      ← Read-only, no auth
         │
         └──► /api/push-call  ← API key required
```

- **Dashboard**: Session-based auth (admin/user roles)
- **Public Map**: Completely open (intentional — public data)
- **API Endpoints**: `X-API-Key` header or session auth
- **Push Endpoint**: Requires matching `PUBLIC_MAP_API_KEY`

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

1. **Database**: Move PostgreSQL to dedicated server or managed service (RDS, Cloud SQL)
2. **Audio Storage**: Use S3-compatible object storage instead of local disk
3. **Transcription**: Use OpenAI Whisper API instead of local model for faster processing
4. **Map Tiles**: Self-host tile server or use commercial tile provider with higher rate limits
5. **Load Balancing**: Run multiple iCAD dispatch containers behind a load balancer

---

*For setup instructions, see the [Quick Start Guide](quickstart.md).*
