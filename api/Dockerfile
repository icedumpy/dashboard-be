FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Keep OS packages minimal so layer download/install stays fast.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first to maximize layer cache hits.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App + Alembic
COPY alembic.ini ./
COPY app ./app

EXPOSE 8000
