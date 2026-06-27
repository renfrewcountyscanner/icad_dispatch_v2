#!/bin/sh
set -e

# Fix ownership of volume mounts at startup — prevents PermissionError
# when log rotation creates files under a different UID on the host.
for dir in /app/log /app/var /app/static/audio; do
    if [ -d "$dir" ]; then
        chown -R icad_dispatch:icad_dispatch "$dir" 2>/dev/null || true
    fi
done

# Drop privileges and run the application as non-root user
exec gosu icad_dispatch "$@"
