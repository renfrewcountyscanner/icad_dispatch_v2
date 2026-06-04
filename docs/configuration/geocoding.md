---
layout: default
title: Geocoding Setup
parent: Configuration
nav_order: 2
---

# Geocoding Setup
{: .no_toc }

Configure address extraction and geocoding for your region.
{: .fs-6 .fw-300 }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

iCAD Dispatch extracts addresses from radio transcripts and converts them to map coordinates.

**Primary provider:** [Nominatim](https://nominatim.org/) (OpenStreetMap) — free, no API key required
**Fallback:** Google Maps Geocoding API — requires API key, paid after free tier

---

## Step 1: Enable Address Extraction

1. Go to **Dashboard → Systems → Your System**
2. Click **Address Extraction** tab
3. Toggle **Enable Address Extraction**
4. Save

---

## Step 2: Configure Geocoding Regions

Geocoding regions tell the system which state/province and counties to accept results from.

### Add Your State/Province

1. In the Address Extraction settings, find **Geocoding Regions**
2. Click **Add Region**
3. Select your state/province from the dropdown

Examples:
- `ON` (Ontario)
- `NY` (New York)
- `TX` (Texas)
- `CA` (California)

### Add Your Counties

1. Click **Add County** within your state
2. Enter county names one per line

Examples:
```
Renfrew County
Ottawa County
Carleton County
```

**Why counties?** The geocoder validates that results fall within your configured counties. This prevents false matches (e.g., a street in another city with the same name).

---

## Step 3: Configure Nominatim

Nominatim is the default geocoder and requires no API key.

### Settings

| Setting | Description | Default |
|---------|-------------|---------|
| **Primary Geocoder** | Select Nominatim | `Nominatim` |
| **Use Bounding Box** | Restrict searches to your configured regions | `Enabled` |
| **User Agent** | Identifies your requests to OSM | `icad_dispatch (your@email.com)` |

### Rate Limits

Nominatim has strict rate limits:
- 1 request per second
- Maximum identifiable traffic

iCAD Dispatch respects these limits automatically. For high-volume dispatch centers, consider:
- Self-hosting Nominatim ([instructions](https://nominatim.org/release-docs/latest/admin/Installation/))
- Using Google Maps as fallback

---

## Step 4: Configure Google Maps (Optional Fallback)

Google Maps provides better results for rural addresses and private roads.

### Get an API Key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable the **Geocoding API**
4. Create an API key under **Credentials**
5. Restrict the key to Geocoding API only

### Add to iCAD

1. Add to your `.env` file:
   ```
   GOOGLE_MAPS_API_KEY=your-api-key-here
   ```
2. Restart containers:
   ```bash
   docker compose -f docker-compose.production.yml restart icad_dispatch
   ```

### Cost Considerations

- Free tier: $200/month credit (approx. 40,000 geocoding requests)
- Paid tier: $5 per 1,000 requests
- Most small-medium agencies stay within free tier

---

## Step 5: Test Geocoding

### Using the Dashboard

1. Go to **Dashboard → Test Upload**
2. Upload a test audio file with a known address
3. Check the extracted address and coordinates
4. View the location on the public map

### Using the API

```bash
curl -X POST https://dispatch.yourdomain.com/api/call-upload \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "audio=@test.mp3" \
  -F "talkgroup=410837" \
  -F "system_id=1"
```

Then check the call in the dashboard or public map.

---

## Troubleshooting

### "Address not found"

- Check the transcript quality — garbled audio = poor extraction
- Verify the county whitelist includes the correct county
- Try enabling Google Maps fallback
- Check Nominatim rate limits in logs

### "Location in wrong county/state"

- Verify county names match exactly (e.g., "Renfrew County" not "Renfrew")
- Ensure the state code is correct (e.g., `ON` not `Ontario`)
- Check bounding box is enabled

### "Geocoding timeout"

- Check internet connectivity from the container
- Verify Nominatim is accessible: `curl https://nominatim.openstreetmap.org/search?q=test&format=json`
- Consider adding Google Maps fallback

### "Slow geocoding"

- Nominatim enforces 1 req/s rate limit
- For high volume, self-host Nominatim or use Google Maps
- Check if address extraction LLM is enabled (adds latency)

---

## Advanced: Self-Hosted Nominatim

For high-volume dispatch centers (100+ calls/day):

```bash
# Using Docker
docker run -d \
  --name nominatim \
  -p 8080:8080 \
  -v nominatim-data:/var/lib/postgresql/14/main \
  mediagis/nominatim:4.2
```

Then update iCAD to use `http://nominatim:8080` instead of the public API.

---

*For notifier configuration, see [Discord](notifiers/discord.md), [Telegram](notifiers/telegram.md), etc.*
