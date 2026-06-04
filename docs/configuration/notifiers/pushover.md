---
layout: default
title: Pushover
parent: Notifiers
grand_parent: Configuration
nav_order: 4
---

# Pushover Setup
{: .no_toc }

Send push notifications to mobile devices.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Pushover sends instant push notifications to iOS and Android devices with:
- Title and message text
- Clickable link to the public map
- Custom notification sounds
- HTML formatting support

---

## Step 1: Create a Pushover Application

1. Go to [Pushover.net](https://pushover.net/)
2. Create an account (if you don't have one)
3. Go to **Your Applications**
4. Click **Create an Application/API Token**
5. Name it "iCAD Dispatch"
6. Copy the **API Token**

---

## Step 2: Get Your User/Group Key

1. In Pushover, go to **Your User Key**
2. Copy the **User Key** (or create a **Group Key** for multiple recipients)

---

## Step 3: Configure in iCAD

1. Go to **Dashboard → Systems → Your System → Pushover**
2. Toggle **Enable Pushover**
3. Fill in:

| Setting | Description |
|---------|-------------|
| **App Token** | Your Pushover application API token |
| **Group Token** | Your Pushover user or group key |
| **Subject** | Title template (e.g., `{system_name} Alert`) |
| **Body** | Message template (e.g., `{trigger_list}`) |
| **Sound** | Notification sound (e.g., `pushover`, `siren`, `magic`) |

### Available Sounds

`pushover`, `bike`, `bugle`, `cashregister`, `classical`, `cosmic`, `falling`, `gamelan`, `incoming`, `intermission`, `magic`, `mechanical`, `pianobar`, `siren`, `spacealarm`, `tugboat`, `alien`, `climb`, `persistent`, `echo`, `updown`, `vibrate`, `none`

---

## Step 4: Per-Trigger Pushover

You can configure different Pushover settings per trigger:

1. Go to **Dashboard → Triggers**
2. Select a trigger
3. Toggle **Enable Pushover**
4. Set per-trigger tokens, sounds, or messages

Useful for:
- Different sounds for Fire vs EMS
- Different recipient groups per station
- Custom messages per trigger type

---

## Step 5: Test

1. Click **Test** in Pushover settings
2. Check your mobile device for the notification
3. Tap it to open the map link

---

## Troubleshooting

### "No notification received"

- Check Do Not Disturb settings on your device
- Verify the app token and user key are correct
- Check Pushover app notification permissions

### "Notification delayed"

- Pushover queues notifications during high volume
- Check Pushover status at [status.pushover.net](https://status.pushover.net/)

---

*For other notifiers, see [Discord](discord.md), [Telegram](telegram.md), [Email](email.md), [n8n](n8n.md), [Make](make.md), or [Ntfy](ntfy.md).*
