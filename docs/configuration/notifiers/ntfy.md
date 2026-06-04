---
layout: default
title: Ntfy
parent: Notifiers
grand_parent: Configuration
nav_order: 7
---

# Ntfy Setup
{: .no_toc }

Send push notifications via Ntfy (self-hosted or ntfy.sh).
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Ntfy is a simple, lightweight pub-sub notification service. iCAD Dispatch sends text notifications to topics, and subscribers receive instant push notifications on their devices.

Features:
- Works on iOS, Android, and web
- Self-hosted or use the public ntfy.sh service
- No complex API setup — just a topic name
- Supports authentication tokens for private topics

---

## Step 1: Choose Your Server

### Option A: Public ntfy.sh (Easiest)

Use the free public server: `https://ntfy.sh`

- No setup required
- Topics are public by default (anyone can subscribe)
- Rate limits apply

### Option B: Self-Hosted (Recommended for Privacy)

Deploy your own Ntfy server:

```bash
docker run -d \
  --name ntfy \
  -p 80:80 \
  -v ntfy-cache:/var/cache/ntfy \
  -v ntfy-data:/etc/ntfy \
  binwiederhier/ntfy serve
```

Or use their [documentation](https://docs.ntfy.sh/) for advanced setup.

---

## Step 2: Choose a Topic

A topic is like a channel name. Pick something unique:

- `yourdepartment-dispatch`
- `county-fire-alerts`
- `station1-paging`

Anyone who knows the topic name can subscribe.

---

## Step 3: Configure in iCAD

1. Go to **Dashboard → Systems → Your System → Ntfy**
2. Toggle **Enable Ntfy**
3. Fill in:

| Setting | Description | Example |
|---------|-------------|---------|
| **Server URL** | Ntfy server address | `https://ntfy.sh` or `https://ntfy.yourdomain.com` |
| **Topic** | Default topic name | `yourdepartment-dispatch` |
| **Token** | Auth token (optional, for private servers) | `tk_...` |

---

## Step 4: Per-Trigger Topics

Each trigger can have its own topic:

1. Go to **Dashboard → Triggers**
2. Select a trigger
3. Set **Ntfy Topic Override**
4. Leave empty to use the system default topic

Example:
- System default: `county-dispatch`
- FIRE trigger: `fire-dispatch`
- EMS trigger: `ems-dispatch`

---

## Step 5: Subscribe on Your Device

### Android / iOS

1. Install the Ntfy app from the app store
2. Add your topic name
3. You'll receive push notifications instantly

### Web

1. Go to `https://ntfy.sh/your-topic-name` (or your self-hosted URL)
2. Allow browser notifications
3. Keep the page open or use the PWA

### CLI

```bash
curl -s ntfy.sh/your-topic-name/json
```

---

## Step 6: Test

1. Click **Test** in Ntfy settings
2. Check your Ntfy app/browser for the notification
3. Verify the message and map link

---

## Troubleshooting

### "No notifications received"

- Topic name has a typo
- Ntfy app is not running in background
- Check notification permissions for the Ntfy app

### "Rate limited"

- Public ntfy.sh has rate limits
- Consider self-hosting for high-volume dispatch centers

---

*For other notifiers, see [Discord](discord.md), [Telegram](telegram.md), [Email](email.md), [Pushover](pushover.md), [n8n](n8n.md), or [Make](make.md).*
