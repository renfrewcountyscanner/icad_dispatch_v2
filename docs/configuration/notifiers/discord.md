---
layout: default
title: Discord
parent: Notifiers
grand_parent: Configuration
nav_order: 1
---

# Discord Setup
{: .no_toc }

Send dispatch alerts to Discord channels via webhooks.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Discord notifications include:
- Rich embeds with call metadata
- Transcript text
- Audio file attachment (optional)
- Static map image showing incident location
- Clickable link to the public map

---

## Step 1: Create a Discord Webhook

1. Open your Discord server
2. Go to the channel where you want alerts
3. Click **Channel Settings** (gear icon)
4. Go to **Integrations → Webhooks**
5. Click **New Webhook**
6. Name it "iCAD Dispatch" (or whatever you prefer)
7. Click **Copy Webhook URL**

The URL looks like:
```
https://discord.com/api/webhooks/1234567890123456789/abcdefghijklmnopqrstuvwxyz
```

---

## Step 2: Configure in iCAD

1. Go to **Dashboard → Systems → Your System → Discord**
2. Toggle **Enable Discord**
3. Paste the **Webhook URL**
4. Configure optional settings:

| Setting | Description | Example |
|---------|-------------|---------|
| **Embed Title** | Title of the Discord embed | `{system_name} Dispatch` |
| **Embed Color** | Hex color of the embed border | `#dc3545` (red) |
| **Embed Footer** | Small text at bottom | `iCAD Dispatch` |
| **Render Map** | Attach a map image | ✅ Recommended |
| **Attach Audio** | Include the audio file | Optional |

---

## Step 3: Add Custom Fields

Discord embeds support up to 25 custom fields. Click **Add Field** to create:

| Field Key | Field Label | Template | Inline |
|-----------|-------------|----------|--------|
| `talkgroup` | Talkgroup | `{talkgroup_name}` | Yes |
| `incident` | Incident | `{incident_category}` | Yes |
| `address` | Address | `{address}` | No |
| `transcript` | Transcript | `{transcript}` | No |

---

## Step 4: Test

1. Click **Test** at the bottom of the Discord settings
2. Check your Discord channel for a test notification
3. Verify the map image, fields, and audio (if enabled)

---

## Per-Trigger Overrides

You can configure Discord per trigger:

1. Go to **Dashboard → Triggers**
2. Select a trigger
3. Toggle **Enable Discord**
4. The trigger will use the system-level webhook and fields by default
5. Custom webhook URLs per trigger are supported

---

## Troubleshooting

### "Webhook returned 404"

- The webhook URL is invalid or was deleted
- Re-create the webhook in Discord and update the URL

### "Message too long"

- Discord has a 2000-character limit for embed descriptions
- Trim long transcripts or split into multiple fields
- Use `{transcript:0:1000}` to truncate in templates

### "Map image not showing"

- Verify `Render Map` is enabled
- Check that `PUBLIC_MAP_BASE_URL` is set correctly in `.env`
- Ensure the public map container is running

---

*For other notifiers, see [Telegram](telegram.md), [Email](email.md), [Pushover](pushover.md), [n8n](n8n.md), [Make](make.md), or [Ntfy](ntfy.md).*
