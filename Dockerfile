FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY fleetproof ./fleetproof
COPY web ./web

# Cloud Run supplies PORT; default for local container runs.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn fleetproof.web:app --host 0.0.0.0 --port ${PORT}"]
