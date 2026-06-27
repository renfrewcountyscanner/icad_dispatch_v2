FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system fonts for crisp marker text rendering
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (same UID/GID as main app for volume permission consistency)
RUN groupadd -g 9911 icad_dispatch && \
    useradd -M -s /usr/sbin/nologin -u 9911 -g icad_dispatch icad_dispatch

WORKDIR /app

COPY public_map/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY public_map/app.py .
COPY public_map/templates/ ./templates/
COPY public_map/static/ ./static/
COPY lib/__init__.py ./lib/__init__.py
COPY lib/map_pin_renderer.py ./lib/map_pin_renderer.py

# Create tile cache directory with correct permissions
RUN mkdir -p /.cache_pin_maps && chown -R icad_dispatch:icad_dispatch /.cache_pin_maps

# Generate 1x1 transparent placeholder PNG for calls without coordinates
RUN python3 -c "from PIL import Image; import os; os.makedirs('/app/static/img', exist_ok=True); img=Image.new('RGBA',(1,1),(0,0,0,0)); img.save('/app/static/img/placeholder.png')"

RUN chown -R icad_dispatch:icad_dispatch /app

USER icad_dispatch

EXPOSE 5000

CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:5000", "--timeout", "120", "app:app"]
