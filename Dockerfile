FROM python:3.11-slim

WORKDIR /app

# System deps (scipy needs BLAS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps — worker-only (no torch, saves 2GB+ on ARM64)
# Bot runs via systemd and has torch installed natively.
COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt

# Copy source
COPY . .

# Runtime dirs
RUN mkdir -p data engine/state engine/logs evolution

# Default: run the engine. Override CMD in docker-compose per service.
CMD ["python", "engine/main_wrapper_simple.py"]
