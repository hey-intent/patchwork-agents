# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080


WORKDIR /srv

# deps système minimales (certs + curl utile en debug)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

# requirements d'abord (cache)
COPY app/requirements.txt /srv/requirements.txt
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r /srv/requirements.txt


# code
COPY app/ /srv/app/

EXPOSE 8080

# app/app.py => module "app.app", variable FastAPI "app"
CMD ["sh", "-c", "uvicorn app.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]