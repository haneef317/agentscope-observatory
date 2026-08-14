# AgentScope Observatory backend image
#
# Multi-stage: builds the React frontend, then produces a small runtime image
# that serves the compiled SPA alongside the FastAPI app and runs DB
# migrations automatically on boot.
#
# Build from the PROJECT ROOT so both backend/ and frontend/ are in context:
#   docker build -f backend/Dockerfile -t agentscope .

FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend .
RUN npm run build

FROM python:3.12-slim
WORKDIR /opt/agentscope

# psycopg binary deps + postgres client for migrations
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app app
# The compiled frontend lands inside the image.
COPY --from=frontend /build/dist frontend/dist

EXPOSE 8000

# Migrations first (idempotent), then uvicorn.
CMD ["sh", "-c", "python -m app.db.init_db && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
