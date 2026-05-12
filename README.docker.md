# Docker Deployment Guide

This document explains how to build and run the API in Docker containers.

## Files Created

- **Dockerfile**: Defines the container image for the API server
- **docker-compose.yml**: Orchestrates the containerized application with proper volume setup
- **api/main.py** (updated): Added `/health` endpoint for container health checks

## Quick Start with Docker Compose

### Prerequisites
- Docker Engine 20.10+
- Docker Compose 2.0+

### Run the Application

```bash
docker-compose up -d
```

This command will:
1. Build the Docker image (if not already built)
2. Create a named volume `db-data` for database persistence
3. Start the API container on port 8000

### Verify the API is Running

```bash
# Check health endpoint
curl http://localhost:8000/health

# Should return: {"status":"ok"}
```

### Access the API

- **API Base URL**: http://localhost:8000
- **Health Check**: http://localhost:8000/health
- **API Documentation**: http://localhost:8000/docs (Swagger UI)

### Stop the Application

```bash
docker-compose down
```

### View Container Logs

```bash
docker-compose logs -f api
```

## Manual Docker Build and Run

### Build the Image

```bash
docker build -t api:latest .
```

### Run a Container

```bash
docker run -d \
  --name api \
  -p 8000:8000 \
  -v db-data:/app/db \
  -e DATABASE_URL=/app/db/cows.db \
  entrevista-api:latest
```

### Stop and Remove Container

```bash
docker stop api
docker rm api
```

## Database Persistence

The database is stored in a Docker volume named `db-data`.

### View Volume Information

```bash
# Using docker-compose
docker volume inspect db-data
```

### Persist Data Between Runs

The volume is automatically created and mounted at `/app/db` inside the container. The SQLite database file is stored at `/app/db/cows.db`.

- **First run**: Database is automatically initialized
- **Subsequent runs**: Existing database is reused (data persists)

## Environment Variables

- `DATABASE_URL`: Path to the SQLite database (default: `/app/db/cows.db`)
- `PYTHONUNBUFFERED`: Set to `1` to disable Python output buffering

## Health Checks

The container includes a built-in health check that:
- Checks the `/health` endpoint every 10 seconds
- Considers the container healthy when the endpoint returns HTTP 200
- Starts health checks after 15 seconds (start period)
- Retries up to 3 times before marking as unhealthy

### View Health Status

```bash
docker ps --filter "name=api"
```

The `STATUS` column will show `(healthy)`, `(unhealthy)`, or `(starting)`.


## API Endpoints (Sample)

- `GET /health` - Health check (returns `{"status":"ok"}`)
- `GET /docs` - Swagger UI documentation
- `GET /insights/health` - Ill cows data
- `POST /cows/{id}` - Create/update cow
- `POST /sensors/{id}` - Create/update sensor
- `POST /load/measurements` - Bulk load measurements

## Database Initialization

On first run, the database is automatically initialized with the proper schema. Subsequent runs will use the existing database.

To reset the database, remove the volume:
```bash
docker-compose down -v
docker-compose up -d
```

---