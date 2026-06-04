# iCAD Dispatch v2

Real-time Emergency Services Dispatch System for Fire, EMS, and Public Safety agencies across North America.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue?logo=docker)](docs/installation/docker.md)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-green)](https://YOUR_GITHUB_USERNAME.github.io/icad_dispatch_v2/)

## What is iCAD Dispatch?

iCAD Dispatch v2 ingests real-time radio audio from emergency services, automatically:

- **Detects tones** (two-tone paging, long-tone, MDC, DTMF)
- **Transcribes speech** using Whisper AI
- **Extracts addresses** from transcripts using LLM + geocoding
- **Classifies incidents** (Fire, Medical, Traffic, Rescue, etc.)
- **Sends notifications** to Discord, Telegram, Email, Pushover, n8n, Make, and Ntfy
- **Displays calls** on a live public map with real-time updates

Built for **North American emergency services** — fire departments, EMS agencies, and dispatch centers.

## Quick Start

```bash
# One-click installer (requires Docker + Git)
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/main/install.sh | bash

# Or clone and configure manually
git clone https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2.git
cd icad_dispatch_v2
cp .env.example .env
# Edit .env with your domain, timezone, and secrets
docker compose -f docker-compose.production.yml up -d
```

**[Read the full Quick Start Guide →](docs/quickstart.md)**

## Features

| Feature | Description |
|---------|-------------|
| **Tone Detection** | Automatic two-tone, long-tone, MDC, and DTMF detection |
| **AI Transcription** | OpenAI Whisper local or API-based speech-to-text |
| **Address Extraction** | LLM-powered address parsing with Nominatim + Google Maps geocoding |
| **Incident Classification** | AI-categorized incident types with confidence scores |
| **Multi-Channel Alerts** | Discord, Telegram, Email, Pushover, n8n, Make, Ntfy |
| **Live Public Map** | Real-time WebSocket map with dark mode, filters, and audio playback |
| **Map Corrections** | Drag-and-drop location correction via dashboard |
| **Call History** | Searchable database with 2000+ call retention |
| **Security** | Rate limiting, CSRF protection, path traversal prevention |

## Architecture

```
Radio Audio ──► iCAD Dispatch ──► PostgreSQL ──► Public Map ──► Browser
                    │                              │
                    └─► Discord                     └─► WebSocket
                    └─► Telegram
                    └─► Email
                    └─► Pushover
                    └─► n8n
                    └─► Make
                    └─► Ntfy
```

**[View full architecture diagram →](docs/architecture.md)**

## Documentation

- **[Quick Start](docs/quickstart.md)** — 15-minute setup with Docker Compose
- **[Docker Installation](docs/installation/docker.md)** — Full Docker guide
- **[Native Installation](docs/installation/native.md)** — Python + PostgreSQL manual setup
- **[One-Click Installer](docs/installation/one-click.md)** — Automated install script
- **[Environment Variables](docs/configuration/env-variables.md)** — Every config option explained
- **[Geocoding Setup](docs/configuration/geocoding.md)** — Nominatim + Google Maps configuration
- **[Notifier Setup](docs/configuration/)** — Discord, Telegram, Email, Pushover, n8n, Make, Ntfy
- **[Public Map](docs/configuration/public-map.md)** — Live map configuration
- **[Security](docs/security.md)** — Hardening checklist
- **[API Reference](docs/api.md)** — REST API documentation
- **[Troubleshooting](docs/troubleshooting.md)** — Common issues and fixes

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 LTS |
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB SSD | 50 GB SSD |
| Network | Public IP or reverse proxy | Dedicated server / VPS |
| Docker | 24.0+ | Latest |

## Screenshots

*(Add screenshots of dashboard, public map, and Discord notification here)*

## License

[MIT License](LICENSE) — free for personal and commercial use.

## Support

- **Docs**: [GitHub Pages](https://YOUR_GITHUB_USERNAME.github.io/icad_dispatch_v2/)
- **Issues**: [GitHub Issues](https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/issues)
- **Discussions**: [GitHub Discussions](https://github.com/YOUR_GITHUB_USERNAME/icad_dispatch_v2/discussions)

---

Built with ❤️ for first responders everywhere.
