---
layout: default
title: n8n
parent: Notifiers
grand_parent: Configuration
nav_order: 5
---

# n8n Setup
{: .no_toc }

Send dispatch data to n8n workflows for advanced automation.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

n8n is a workflow automation platform. iCAD Dispatch sends a JSON payload to your n8n webhook, and you can build workflows that:
- Forward to Slack, Teams, or custom APIs
- Log to Google Sheets or Airtable
- Trigger SMS via Twilio
- Post to social media
- Anything n8n supports

The payload includes:
- Call metadata (ID, timestamp, duration)
- System and talkgroup info
- Trigger names
- Transcript text
- Audio URL
- Address and map image URL
- Incident classification

---

## Step 1: Create an n8n Webhook

1. Go to your n8n instance (self-hosted or [n8n.cloud](https://n8n.cloud/))
2. Create a new workflow
3. Add a **Webhook** node as the trigger
4. Set **HTTP Method** to `POST`
5. Set **Authentication** to `None` (iCAD signs with JWT)
6. Copy the **Webhook URL**

---

## Step 2: Configure in iCAD

1. Go to **Dashboard → Systems → Your System → n8n**
2. Toggle **Enable n8n**
3. Fill in:

| Setting | Description | Example |
|---------|-------------|---------|
| **Webhook URL** | Your n8n webhook URL | `https://your-n8n.app/webhook/icad` |
| **JWT Passphrase** | Secret for signing JWT | Generate with `openssl rand -hex 32` |
| **JWT Issuer** | Token issuer claim | `icad_dispatch` |
| **JWT Audience** | Token audience claim | `n8n` |
| **JWT TTL** | Token lifetime in seconds | `300` (5 minutes) |

---

## Step 3: Verify JWT in n8n (Optional but Recommended)

In your n8n workflow, add a **Function** node after the webhook to validate the JWT:

```javascript
const jwt = require('jsonwebtoken');
const token = $input.first().json.headers.authorization.replace('Bearer ', '');

try {
  const decoded = jwt.verify(token, 'YOUR_JWT_PASSPHRASE', {
    issuer: 'icad_dispatch',
    audience: 'n8n'
  });
  return [{ json: { valid: true, data: decoded }}];
} catch (error) {
  return [{ json: { valid: false, error: error.message }}];
}
```

---

## Step 4: Test

1. Click **Test** in n8n settings
2. Check your n8n workflow execution history
3. Verify the payload structure

---

## Example Payload

```json
{
  "radio_system_id": 1,
  "system_name": "County Fire",
  "talkgroup_id": 410837,
  "talkgroup_name": "PAGING",
  "trigger_names": ["FIRE - Station 1"],
  "trigger_list": "FIRE - Station 1",
  "trigger_count": 1,
  "timestamp": "2024-01-15 14:30:00",
  "timestamp_24": "14:30:00",
  "audio_url": "https://yourdomain.com/audio/1_410837_12345.mp3",
  "transcript_text": "Station 1, respond to 123 Main Street for a structure fire...",
  "duration_s": 45.2,
  "address": "123 Main Street, Your City",
  "address_lat": 40.7589,
  "address_lng": -73.9851,
  "map_image_url": "https://map.yourdomain.com/static/public_maps/map1.png",
  "incident_category": "Fire"
}
```

---

## Troubleshooting

### "Webhook returned 404"

- Wrong webhook URL
- n8n webhook node is not active
- Check webhook execution history in n8n

### "JWT validation failed"

- Mismatched passphrase between iCAD and n8n
- Token expired (check TTL)
- Wrong issuer/audience claims

---

*For other notifiers, see [Discord](discord.md), [Telegram](telegram.md), [Email](email.md), [Pushover](pushover.md), [Make](make.md), or [Ntfy](ntfy.md).*
