FROM python:3.11-slim

WORKDIR /app

# System deps (FinBERT needs torch, scipy needs BLAS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Runtime dirs
RUN mkdir -p data state logs

# Default: run the engine. Override CMD in docker-compose per service.
CMD ["python", "engine/main_wrapper_simple.py"]
