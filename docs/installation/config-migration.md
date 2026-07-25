---
layout: default
title: Configuration Migration
parent: Installation
nav_order: 4
---

# Configuration Migration

The repository includes `scripts/config_migrate.py` for moving operational
configuration between iCAD Dispatch instances. It exports radio systems,
tones, triggers, notification integrations, transcription, geocoding, storage,
incident classification, and talkgroup routing.

It does not export call history, audio files, trigger-fire history, runtime
rate limits, sessions, remember-me tokens, or user passwords. Create the
target admin account from its `.env` file and recreate any non-root users.

## Export from the source instance

Run this from the repository directory on the source VM:

```bash
docker compose -f docker-compose.production.yml exec -T icad_dispatch \
  python3 scripts/config_migrate.py export \
  --output /app/var/icad-config.json
```

The file appears as `var/icad-config.json` on the host because `var/` is a
mounted volume. It contains integration credentials and is written with mode
`0600`; transfer it over a secure channel and do not commit it to GitHub.

For a shareable template without credentials:

```bash
docker compose -f docker-compose.production.yml exec -T icad_dispatch \
  python3 scripts/config_migrate.py export \
  --output /app/var/icad-config-redacted.json \
  --redact-secrets
```

## Import into another instance

1. Clone the repository on the target VM and create `.env` from `.env.example`.
2. Set a unique `PG_PASSWORD`, `ROOT_PASSWORD`, `PUBLIC_MAP_API_KEY`, and
   `MAP_SECRET_KEY`. Set `BASE_URL` and the bind addresses for the new VM.
3. Start the full stack and wait for PostgreSQL and `icad_dispatch` to become
   healthy:

```bash
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
```

4. Copy the export into the target repository's `var/` directory, then validate
   it without changing the database:

```bash
docker compose -f docker-compose.production.yml exec -T icad_dispatch \
  python3 scripts/config_migrate.py import \
  --input /app/var/icad-config.json --dry-run
```

5. Import it after the dry run succeeds:

```bash
docker compose -f docker-compose.production.yml exec -T icad_dispatch \
  python3 scripts/config_migrate.py import \
  --input /app/var/icad-config.json
```

Imports match radio systems by `system_decimal`, map child settings to the
target system IDs, and replace the exported recipient, geocoding, tone-rule,
and trigger-rule collections for those systems. Trigger names are used to
match existing triggers; importing into a freshly initialized target is the
most predictable option.

If the export was redacted, credentials remain unset or unchanged on the
target and must be configured separately. A full export includes secrets and
should be treated like a password.

## Deployment checklist

Before exposing the target VM:

- Change every placeholder secret in `.env`.
- Keep PostgreSQL bound to `127.0.0.1` or an internal-only interface.
- Put ports 9911 and 5000 behind the reverse proxy described in the Docker
  installation guide.
- Confirm `PUBLIC_MAP_API_KEY` matches between `icad_dispatch` and `public_map`.
- Run `docker compose -f docker-compose.production.yml ps` and confirm the
  app and database are healthy.
- Keep the export file and database backups outside the Git repository.

The test Compose file uses a separate project name so running the test stack
cannot replace the production `postgres` or `icad_dispatch` containers.
