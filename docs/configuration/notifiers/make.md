---
layout: default
title: Make
parent: Notifiers
grand_parent: Configuration
nav_order: 6
---

# Make (Integromat) Setup
{: .no_toc }

Send dispatch data to Make.com webhooks.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Make.com (formerly Integromat) is a no-code automation platform. iCAD Dispatch sends a JSON payload to your Make webhook, and you can build scenarios that:
- Forward to Slack, Microsoft Teams, or Discord
- Log to Google Sheets, Notion, or Airtable
- Trigger SMS via Twilio
- Create calendar events
- Anything Make supports

---

## Step 1: Create a Make Webhook

1. Go to [Make.com](https://www.make.com/)
2. Create a new scenario
3. Add **Webhooks → Custom webhook** as the trigger
4. Click **Add** to create a new webhook
5. Name it "iCAD Dispatch"
6. Copy the **Webhook URL**

---

## Step 2: Configure in iCAD

1. Go to **Dashboard → Systems → Your System → Make**
2. Toggle **Enable Make**
3. Fill in:

| Setting | Description | Example |
|---------|-------------|---------|
| **Webhook URL** | Your Make webhook URL | `https://hook.make.com/abc123...` |
| **API Key** | Optional webhook secret | From Make webhook settings |

---

## Step 3: Add Custom Fields

Make scenarios work best with structured data. Add fields in iCAD:

| Field Key | Field Value Template |
|-----------|----------------------|
| `call_id` | `{call_id}` |
| `timestamp` | `{timestamp}` |
| `system_name` | `{system_name}` |
| `talkgroup` | `{talkgroup_name}` |
| `triggers` | `{trigger_list}` |
| `address` | `{address}` |
| `transcript` | `{transcript}` |
| `audio_url` | `{audio_url}` |
| `map_url` | `{map_image_url}` |
| `incident` | `{incident_category}` |

---

## Step 4: Test

1. Click **Test** in Make settings
2. In Make, click **Run once** on your scenario
3. Verify the data appears in Make's execution log

---

## Troubleshooting

### "Webhook not triggered"

- Make scenario is not active
- Webhook URL is wrong
- Check Make's execution history

### "Data not parsed correctly"

- Make may need the "JSON - Parse JSON" module
- Or set the webhook to "JSON" content type

---

*For other notifiers, see [Discord](discord.md), [Telegram](telegram.md), [Email](email.md), [Pushover](pushover.md), [n8n](n8n.md), or [Ntfy](ntfy.md).*
