---
layout: default
title: API Reference
nav_order: 8
---

# API Reference
{: .no_toc }

REST API endpoints for integration.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Authentication

Most endpoints require either:
- **Session cookie** — for dashboard/browser access
- **API Key** — for external integrations

### API Key Header

```
X-API-Key: your-system-api-key
```

Obtain your system API key from the dashboard: **Systems → Your System → API Key**

---

## Call Upload

### POST `/api/call-upload`

Upload a radio audio clip for processing.

**Headers:**
```
X-API-Key: your-api-key
Content-Type: multipart/form-data
```

**Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio` | File | Yes | Audio file (MP3, WAV, OGG) |
| `talkgroup` | Integer | Yes | Talkgroup ID |
| `system_id` | Integer | Yes | Radio system ID |
| `duration_s` | Float | No | Audio duration in seconds |
| `start_epoch_s` | Integer | No | Unix timestamp of call start |

**Response:**
```json
{
  "success": true,
  "call_id": 12345,
  "transcript": "Station 1, respond to 123 Main Street...",
  "triggers_fired": ["FIRE - Station 1"]
}
```

**cURL Example:**
```bash
curl -X POST https://dispatch.yourdomain.com/api/call-upload \
  -H "X-API-Key: your-api-key" \
  -F "audio=@/path/to/call.mp3" \
  -F "talkgroup=410837" \
  -F "system_id=1"
```

---

## Call Retrieval

### GET `/api/calls`

Retrieve call history.

**Parameters:**

| Query | Type | Default | Description |
|-------|------|---------|-------------|
| `hours` | Float | 24 | Hours to look back |
| `from` | Integer | — | Start epoch timestamp |
| `to` | Integer | — | End epoch timestamp |
| `system_id` | Integer | — | Filter by radio system |
| `incident` | String | — | Filter by incident category |
| `alert_trigger_ids` | String | — | Comma-separated trigger IDs |

**Response:**
```json
{
  "success": true,
  "result": [
    {
      "call_id": 12345,
      "timestamp": 1705330200,
      "datetime": "2024-01-15 14:30:00",
      "duration_s": 45.2,
      "talkgroup": 410837,
      "talkgroup_name": "PAGING",
      "system_id": 1,
      "system_name": "County Fire",
      "transcript": "Station 1, respond...",
      "incident_category": "Fire",
      "lat": 40.7589,
      "lng": -73.9851,
      "address": "123 Main Street, Your City",
      "audio_url": "https://yourdomain.com/audio/...",
      "is_corrected": false
    }
  ],
  "meta": {
    "hours": 24,
    "count": 1
  }
}
```

---

## Call Detail

### GET `/api/calls/{call_id}`

Retrieve a single call by ID.

**Response:** Same as `/api/calls` but single object in `result`.

---

## Triggers

### GET `/api/triggers`

List enabled alert triggers.

**Parameters:**

| Query | Type | Description |
|-------|------|-------------|
| `system_id` | Integer | Filter by radio system |

**Response:**
```json
{
  "success": true,
  "result": [
    {
      "alert_trigger_id": 1,
      "alert_trigger_name": "FIRE - Station 1",
      "radio_system_id": 1
    }
  ]
}
```

---

## Public Map Push

### POST `/api/push-call`

Push a call to the public map (used internally by iCAD).

**Headers:**
```
X-API-Key: public-map-api-key
Content-Type: application/json
```

**Body:**
```json
{
  "calls": [
    {
      "call_id": 12345,
      "timestamp": 1705330200,
      "incident_category": "Fire",
      "lat": 40.7589,
      "lng": -73.9851,
      "address": "123 Main Street"
    }
  ]
}
```

---

## Health Check

### GET `/health`

Check service health.

**Response:**
```json
{
  "status": "ok",
  "calls_total": 1523,
  "timestamp": 1705330200
}
```

---

## Systems

### GET `/api/systems`

List radio systems (requires authentication).

**Response:**
```json
{
  "success": true,
  "result": [
    {
      "radio_system_id": 1,
      "system_name": "County Fire",
      "system_decimal": 12345,
      "api_key": "abc123..."
    }
  ]
}
```

---

## Tone Finder

### GET `/api/tone-finder/calls`

List calls with tone detection results.

### POST `/api/tone-finder/reprocess-triggers`

Reprocess triggers for recent calls.

---

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| `/api/calls` | 60 req/min |
| `/api/triggers` | 60 req/min |
| `/api/call-upload` | 60 req/min |
| `/api/push-call` | 60 req/min |

Rate limit responses:
```json
{
  "success": false,
  "message": "Rate limit exceeded"
}
```

---

## Error Responses

All endpoints return structured errors:

```json
{
  "success": false,
  "message": "Human-readable error description"
}
```

Common HTTP status codes:

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request (missing parameters) |
| 401 | Unauthorized (invalid API key) |
| 404 | Not found |
| 429 | Rate limit exceeded |
| 500 | Server error |

---

*For setup instructions, see [Quick Start](quickstart.md).*
