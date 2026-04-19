# iCAD Dispatch v2.5

[![Docker Image Version](https://img.shields.io/docker/v/renfrewcountyscanner/icad_dispatch_v2/latest)](https://github.com/renfrewcountyscanner/icad_dispatch_v2/pkgs/container/icad_dispatch_v2)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/renfrewcountyscanner/icad_dispatch_v2)](https://github.com/renfrewcountyscanner/icad_dispatch_v2)

iCAD Dispatch is a Progressive Web Application (PWA) that processes audio inputs from a police scanner application and triggers alerts based on tones found in the audio.

---

## Quick Start

### Option 1: One-Command Setup (Recommended)

```bash
curl -sL https://raw.githubusercontent.com/renfrewcountyscanner/icad_dispatch_v2/main/setup.sh | bash
```

This script will:
- Create the required user/group
- Set up directory structure
- Create configuration
- Start the container

### Option 2: Manual Docker Compose

```bash
# Clone the repository
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git
cd icad_dispatch_v2

# Copy and edit environment file
cp .env_example .env
nano .env  # Edit with your settings

# Start the application
docker compose up -d

# Access at http://localhost:9911
# Default login: root / changeme123
```

---

## Features

### v2.5 Highlights

- **User System Access Control** - Create users with per-system permissions (read/write)
- **Tone Hits: Up to 1000 Entries** - Increased page size for large datasets
- **Version Display** - Current version shown in footer

### Core Features

- **Tone Detection** - Two-tone, long tone, hi/low, pulsed, DTMF, and MDC support
- **Trigger System** - Custom alerts based on detected tones
- **Audio Processing** - WebRTC VAD, transcriber, and audio normalization
- **Multiple Radio Systems** - Support for multiple scanner systems
- **Notifications** - Pushover, Discord, Telegram, Email, n8n webhooks
- **Post-Tone Delay** - Trim audio after tones are detected
- **Live Audio Streaming** - Real-time audio playback

### Security

- Session-based authentication
- Role-based access control (admin, user)
- Non-root Docker container
- CSRF protection

---

## Requirements

- **Linux**: Developed on Debian. Other distros should work.
- **Docker**: [Install Docker](https://docs.docker.com/get-docker/)
- **Git**: [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)

---

## Deployment

### Using GitHub Container Registry (GHCR)

The recommended way to deploy:

```bash
git clone https://github.com/renfrewcountyscanner/icad_dispatch_v2.git
cd icad_dispatch_v2

# Edit configuration
cp .env_example .env
nano .env

# Start (pulls image from GHCR)
docker compose up -d
```

### Using Docker Hub (Alternative)

```yaml
# In docker-compose.yml, change:
image: ghcr.io/renfrewcountyscanner/icad_dispatch_v2:latest
# to:
image: renfrewcountyscanner/icad_dispatch_v2:latest
```

### Local Development Build

For development or custom builds:

```bash
# Use the production compose file
docker compose -f docker-compose.production.yml up -d --build
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | 1=DEBUG, 2=INFO, 3=WARNING, 4=ERROR | 2 |
| `TIMEZONE` | IANA timezone | America/New_York |
| `BASE_URL` | Public base URL | http://localhost:9911 |
| `SESSION_COOKIE_SECURE` | Use HTTPS cookies | False |
| `SESSION_COOKIE_DOMAIN` | Cookie domain | (server IP) |
| `SQLITE_DATABASE_PATH` | Database path | var/icad_dispatch.db |
| `ROOT_USERNAME` | Admin username | root |
| `ROOT_PASSWORD` | Admin password | (required) |

### Ports

- **9911** - Main application port

### Volumes

- `./log` - Application logs
- `./var` - SQLite database
- `./audio` - Stored audio files

---

## User Management

As of v2.5, iCAD Dispatch supports granular user permissions:

1. **Admin Users** - Full access to all systems and user management
2. **Standard Users** - Can be assigned to specific radio systems
3. **Permission Levels**:
   - **Read** - View system data, cannot make changes
   - **Write** - Full access to assigned systems

### Creating Users

1. Log in as admin
2. Navigate to Admin section
3. Create new users and assign systems/permissions

---

## Upgrading

### Using the Setup Script

```bash
cd /opt/icad_dispatch
sudo ./setup.sh --update
```

### Manual Update

```bash
cd icad_dispatch_v2
docker compose pull
docker compose up -d
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs icad_dispatch

# Common issues:
# - Port already in use: Change port in docker-compose.yml
# - Permission errors: Ensure directories are owned by icad_dispatch user
```

### Database Migration Issues

```bash
# Backup database
cp var/icad_dispatch.db var/icad_dispatch.db.backup

# Check logs for migration errors
docker logs icad_dispatch | grep -i migration
```

### Audio Not Saving

```bash
# Check permissions
ls -la audio/

# Should show: icad_dispatch:icad_dispatch

# Fix if needed
sudo chown -R icad_dispatch:icad_dispatch audio
```

### Login Issues

```bash
# Reset root password in .env
ROOT_PASSWORD=your_new_password

# Restart container
docker compose restart
```

---

## Support

- **Issues**: [GitHub Issues](https://github.com/renfrewcountyscanner/icad_dispatch_v2/issues)
- **Discussions**: [GitHub Discussions](https://github.com/renfrewcountyscanner/icad_dispatch_v2/discussions)

---

## Contributing

Contributions are welcome! Please read the [CONTRIBUTING](CONTRIBUTING.md) guidelines before submitting pull requests.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Credits

- Developed for scanner enthusiasts and emergency services
- Thanks to all contributors!