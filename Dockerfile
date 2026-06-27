FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libavcodec-extra \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 9911 icad_dispatch && \
    useradd -M -s /usr/sbin/nologin -u 9911 -g icad_dispatch icad_dispatch

# Set work directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY lib/ ./lib/
COPY routes/ ./routes/
COPY templates/ ./templates/
COPY static/ ./static/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# Entrypoint fixes volume mount ownership at startup
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Set ownership
RUN chown -R icad_dispatch:icad_dispatch /app

# Expose port
EXPOSE 9911

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests, sys; r = requests.get('http://localhost:9911/'); sys.exit(0 if r.status_code in (200, 302) else 1)" || exit 1

# Entrypoint fixes permissions then drops to non-root user
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:9911", "--workers", "4", "--threads", "2", "--timeout", "120", "app:app"]
