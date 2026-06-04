---
layout: default
title: Telegram
parent: Notifiers
grand_parent: Configuration
nav_order: 2
---

# Telegram Setup
{: .no_toc }

Send dispatch alerts to Telegram channels as voice messages.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Telegram notifications are sent as **voice messages** (Opus OGG format) with a text caption containing:
- Timestamp
- Trigger names
- Transcript excerpt
- Map image URL (if available)

---

## Step 1: Create a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`
3. Follow prompts to name your bot (e.g., "iCAD Dispatch")
4. Copy the **HTTP API token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

---

## Step 2: Create a Channel

1. In Telegram, create a new channel (or use an existing group)
2. Add your bot as an administrator with permission to post messages
3. Get the channel ID:
   - Forward a message from the channel to [@userinfobot](https://t.me/userinfobot)
   - Or use [@raw_info_bot](https://t.me/raw_info_bot)
   - The ID looks like `-1001234567890` (include the `-100` prefix)

---

## Step 3: Configure in iCAD

1. Go to **Dashboard → Systems → Your System → Telegram**
2. Toggle **Enable Telegram**
3. Paste the **Bot Token**
4. Paste the **Channel ID**
5. Customize the **Message Body** template:

```
{timestamp}
{trigger_list}
{transcript}
iCAD Dispatch
```

---

## Step 4: Test

1. Click **Test** in Telegram settings
2. Check your Telegram channel for a voice message
3. Verify the caption and map link

---

## Troubleshooting

### "Bot not authorized"

- The bot is not an admin in the channel
- Add the bot as an administrator with post permissions

### "Channel not found"

- Wrong channel ID format
- Must include `-100` prefix for channels
- Example: `-1001234567890` not `1234567890`

### "ffmpeg not found"

- Install ffmpeg: `sudo apt-get install ffmpeg`
- Or ensure ffmpeg is in the Docker image (already included in official image)

---

*For other notifiers, see [Discord](discord.md), [Email](email.md), [Pushover](pushover.md), [n8n](n8n.md), [Make](make.md), or [Ntfy](ntfy.md).*
