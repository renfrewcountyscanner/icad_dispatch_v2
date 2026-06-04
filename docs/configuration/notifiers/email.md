---
layout: default
title: Email
parent: Notifiers
grand_parent: Configuration
nav_order: 3
---

# Email Setup
{: .no_toc }

Send dispatch alerts via SMTP email.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Email notifications include:
- HTML body with embedded map image (if available)
- Transcript text
- Call metadata (time, talkgroup, address)
- Plain text fallback for email clients that don't render HTML

---

## Step 1: Configure SMTP Settings

1. Go to **Dashboard → Systems → Your System → Email**
2. Fill in:

| Setting | Description | Example |
|---------|-------------|---------|
| **SMTP Host** | Your email server | `smtp.gmail.com` |
| **SMTP Port** | Server port | `587` (STARTTLS) or `465` (SSL) |
| **SMTP Username** | Login username | `your-email@gmail.com` |
| **SMTP Password** | Login password or app password | `your-app-password` |
| **From Address** | Sender email | `dispatch@yourdomain.com` |
| **From Name** | Sender display name | `iCAD Dispatch` |

---

## Step 2: Add Recipients

1. In the Email settings, find **Recipients**
2. Click **Add Recipient**
3. Enter email addresses

Formats supported:
- `user@example.com`
- `Name <user@example.com>`

---

## Step 3: Customize Templates

### Subject Template

```
[{incident_category}] Dispatch Alert — {system_name}
```

### Body Template (HTML)

```html
<h3>{incident_category} — {timestamp}</h3>
<p><strong>Talkgroup:</strong> {talkgroup_name}</p>
<p><strong>Address:</strong> {address}</p>
<p><strong>Transcript:</strong></p>
<blockquote>{transcript}</blockquote>
<p><a href="{audio_url}">Listen to Audio</a></p>
```

---

## Step 4: Privacy Mode

Choose how emails are sent:

| Mode | Description | Use Case |
|------|-------------|----------|
| **BCC** | Single email, all recipients hidden | Default, protects recipient privacy |
| **Per Recipient** | Individual email per recipient | Personalized content per person |

---

## Step 5: Test

1. Click **Test** in Email settings
2. Check recipient inboxes
3. Verify HTML rendering, map image, and links

---

## Gmail-Specific Setup

Gmail requires an **App Password** instead of your regular password:

1. Go to [Google Account Settings](https://myaccount.google.com/)
2. Security → 2-Step Verification → enable it
3. Security → App passwords → Generate new
4. Select "Mail" and "Other (Custom name)"
5. Copy the 16-character password
6. Use this as your SMTP password in iCAD

---

## Troubleshooting

### "Authentication failed"

- Wrong username/password
- For Gmail: use App Password, not regular password
- For Office 365: use "Modern Auth" or app-specific password

### "Connection refused"

- Wrong SMTP host or port
- Firewall blocking outbound SMTP
- Try port 587 with STARTTLS instead of 465

### "Emails going to spam"

- Use a custom domain email (not @gmail.com)
- Set up SPF, DKIM, and DMARC records
- Use a transactional email service (SendGrid, Mailgun)

---

*For other notifiers, see [Discord](discord.md), [Telegram](telegram.md), [Pushover](pushover.md), [n8n](n8n.md), [Make](make.md), or [Ntfy](ntfy.md).*
