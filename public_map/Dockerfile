FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system fonts for crisp marker text rendering
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY public_map/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY public_map/app.py .
COPY public_map/templates/ ./templates/
COPY public_map/static/ ./static/
COPY lib/__init__.py ./lib/__init__.py
COPY lib/map_pin_renderer.py ./lib/map_pin_renderer.py

EXPOSE 5000

CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:5000", "--timeout", "120", "app:app"]
