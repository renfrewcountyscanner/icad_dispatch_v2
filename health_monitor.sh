#!/bin/bash
# iCAD Dispatch Health Monitor
# Checks if the service is responding and restarts if needed
# Add to crontab: */5 * * * * /app/icad_dispatch_v2/health_monitor.sh >> /app/icad_dispatch_v2/log/health_monitor.log 2>&1

APP_URL="http://localhost:9911"
CONTAINER_NAME="icad_dispatch_v2-icad_dispatch-1"
LOG_FILE="/app/icad_dispatch_v2/log/health_monitor.log"
MAX_RETRIES=3
RETRY_DELAY=5

# Create log file if it doesn't exist
touch "$LOG_FILE"

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

check_service() {
    local attempt=$1
    local response
    local http_code
    
    response=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL" 2>/dev/null)
    http_code="${response:-000}"
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "302" ]; then
        return 0
    else
        log_message "WARNING: Health check attempt $attempt failed (HTTP $http_code)"
        return 1
    fi
}

# Main health check logic
for attempt in $(seq 1 $MAX_RETRIES); do
    if check_service "$attempt"; then
        # Service is healthy
        if [ "$attempt" -gt 1 ]; then
            log_message "INFO: Service recovered after $attempt attempts"
        fi
        exit 0
    fi
    
    if [ "$attempt" -lt "$MAX_RETRIES" ]; then
        sleep "$RETRY_DELAY"
    fi
done

# All retries failed - restart container
log_message "CRITICAL: Service unreachable after $MAX_RETRIES attempts. Restarting container..."

if docker restart "$CONTAINER_NAME" >> "$LOG_FILE" 2>&1; then
    log_message "INFO: Container restarted successfully"
    
    # Wait and verify
    sleep 10
    if check_service "verify"; then
        log_message "INFO: Service confirmed healthy after restart"
    else
        log_message "ERROR: Service still unhealthy after restart!"
    fi
else
    log_message "ERROR: Failed to restart container!"
fi
