# Cattle Farm Management System

A FastAPI-based REST API for managing  farm data, including cow information, sensor measurements, and health monitoring.

## Prerequisites

- Python 3.14+
- UV package manager
- SQLite3
- Docker and Docker Compose (optional, for containerized deployment)

## Installation

1. Navigate to the project directory:
```bash
cd entrevista
```

2. Install dependencies using UV:
```bash
uv sync
```

## Quick Start

Follow these steps to get the application running:

### Step 1: Initialize the Database

Create the SQLite database schema:

```bash
uv run python init_db.py
```

This creates the `cows.db` file with three tables:
- `cow`: Cow metadata (id, name, birthdate)
- `sensor`: Sensor definitions (id, unit)
- `measurement`: Time-series measurement data (sensor_id, cow_id, timestamp, value)

**Output:**
```
INFO Database initialized at 'cows.db'.
Database 'cows.db' initialized successfully.
```

### Step 2: Load Data from Parquet Files

Load the sample data from Parquet files into the database:

```bash
uv run python loader.py
```

The loader processes data in this order:
1. `data/cows.parquet` → POST /cows/{id}
2. `data/sensors.parquet` → POST /sensors/{id}
3. `data/measurements.parquet` → POST /load/measurements

**Output:**
```
INFO 'cow': 50 rows processed.
INFO 'sensor': 2 rows processed.
INFO 'measurement': 1234 rows processed.
```

### Step 3: Start the API Server

Run the FastAPI development server:

```bash
uv run uvicorn api.main:app --reload
```

**Output:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

The API is now available at:
- **API Documentation**: http://localhost:8000/docs (Swagger UI)
- **Alternative Docs**: http://localhost:8000/redoc (ReDoc)
- **Health Check**: http://localhost:8000/health

## API Endpoints

### Cow Management
- `POST /cows/{id}` - Create a cow
- `GET /cows/{id}` - Get cow details with latest measurement

### Sensor Management
- `POST /sensors/{id}` - Create a sensor

### Measurements
- `POST /measurements` - Record a single measurement
- `POST /load/measurements` - Bulk load measurements from Parquet

### Bulk Load Endpoints
- `POST /load/cows` - Bulk load cows from Parquet
- `POST /load/sensors` - Bulk load sensors from Parquet

### Insights & Reports
- `GET /insights/milk-production` - Milk production per cow per day (last 30 days)
- `GET /insights/weights` - Current weight and 30-day average per cow
- `GET /insights/health` - Potentially ill cows (milk drop or weight loss detected)
- `GET /reports/daily?date=YYYY-MM-DD` - Daily farm report in plain text

## Running Tests

Run the complete test suite:

```bash
uv run pytest
```

Run tests with verbose output:

**Test Files:**
- `tests/test_cow_endpoint.py` - Cow API endpoint tests
- `tests/test_sensor_endpoint.py` - Sensor API endpoint tests
- `tests/test_measurement_endpoint.py` - Measurement API endpoint tests
- `tests/test_insights_endpoints.py` - Insights endpoint tests
- `tests/test_load_endpoints.py` - Bulk load endpoint tests
- `tests/test_loader.py` - Data loader validation tests
- `tests/test_measurement_loader.py` - Measurement loader tests
- `tests/test_sensor_loader.py` - Sensor loader tests
- `tests/test_report.py` - Report generation tests
- `tests/test_init_db.py` - Database initialization tests

## Report Generation

### Manual Report Generation

Generate a report for a specific date:

```bash
uv run python -m api.report --date 2025-05-11
```

Output to a file:

```bash
uv run python -m api.report --date 2025-05-11 --output reports/report-2025-05-11.txt
```

Specify custom database path:

```bash
uv run python -m api.report --date 2025-05-11 --db data/cows.db
```

Generate report for today (default):

```bash
uv run python -m api.report
```

### Via API Endpoint

Get daily report via HTTP:

```bash
curl http://localhost:8000/reports/daily?date=2025-05-11
```

Get today's report:

```bash
curl http://localhost:8000/reports/daily
```

### Scheduling with Cron

#### Option A: System Cron (Simple, Single-Server)

1. Create a reports directory:
```bash
mkdir -p reports
```

2. Open crontab editor:
```bash
crontab -e
```

3. Add this line to generate a daily report at 6:00 AM:
```cron
0 6 * * * cd /path/to/entrevista && uv run python -m api.report --output reports/$(date +\%Y-\%m-\%d).txt >> logs/report.log 2>&1
```

4. Create log directory:
```bash
mkdir -p logs
```

5. Verify the cron job is installed:
```bash
crontab -l
```

#### Option B: APScheduler (Self-Contained, Inside FastAPI)

Add scheduling to your FastAPI application for automatic daily report generation. This approach doesn't require external cron setup.

See the `report.py` docstring for implementation examples.

## Environment Variables

Configure behavior using environment variables:

```bash
# Database path (default: data/cows.db)
export DATABASE_URL=path/to/cows.db

# API host and port
export API_HOST=0.0.0.0
export API_PORT=8000
```

## Docker Deployment

Build and run in Docker (see [README.docker.md](README.docker.md) for details):

```bash
docker-compose up -d
```

Check health:

```bash
curl http://localhost:8000/health
```

View logs:

```bash
docker-compose logs -f api
```

Stop the service:

```bash
docker-compose down
```

## Project Structure

```
entrevista/
├── api/
│   ├── __init__.py
│   ├── main.py           # FastAPI app and route handlers
│   ├── models.py         # Pydantic request/response models
│   ├── validation.py     # Validation rules and reports
│   └── report.py         # Daily farm report generator
├── tests/                # Test suite (10 test files)
├── data/
│   ├── cows.parquet      # Sample cow data
│   ├── sensors.parquet   # Sample sensor data
│   └── measurements.parquet  # Sample measurement data
├── loader.py             # Parquet → SQLite pipeline
├── init_db.py            # Database schema initialization
├── Dockerfile            # Container image definition
├── docker-compose.yml    # Container orchestration
├── CLAUDE.md             # Development guidelines
├── README.md             # This file
└── README.docker.md      # Docker deployment guide
```

## Data Loading Flow

```
Parquet Files (data/)
       ↓
  Loaders (loader.py)
       ↓
  Validation & Filtering
       ↓
  API Endpoints (POST /cows, /sensors, /measurements)
       ↓
  SQLite Database (cows.db)
```

## Workflow Example

Complete workflow from initialization to report generation:

```bash
# 1. Initialize database
uv run python init_db.py

# 2. Load sample data
uv run python loader.py

# 3. Start the API server (in another terminal)
uv run uvicorn api.main:app --reload

# 4. Generate a report
uv run python -m api.report

# 5. Run tests
uv run pytest

# 6. Explore API documentation
# Open http://localhost:8000/docs in your browser
```

## Data Validation

The system validates data at multiple levels:

### Loader Validation (`loader.py`)
- **Cows**: Rejects null/blank IDs and names, future birthdates, duplicates
- **Sensors**: Rejects null values, duplicate IDs, invalid units
- **Measurements**: Rejects null/negative values, future timestamps, duplicate composite keys

### API Validation (`api/models.py`)
- Pydantic type checking and field validation
- Sensor unit must be in VALID_UNITS (`L`, `kg`)
- Measurement value must be non-negative

### Database Constraints (`init_db.py`)
- Primary keys prevent duplicate entries
- Foreign keys ensure referential integrity
- Composite key on measurements (sensor_id, cow_id, timestamp)

## Report Features

The daily farm report includes three sections:

### 1. Milk Production
Daily milk production (liters) per cow for the specified date.

### 2. Weight Summary
- Current weight (most recent measurement)
- 30-day average weight
- Status warning if current < 95% of 30-day average

### 3. Health Alerts
Detects potentially ill cows based on:
- **Milk Production Drop**: Recent avg < 70% of baseline (last 3 days vs days 4-30)
- **Weight Loss**: Current weight < 95% of baseline (days 4-30)

## Troubleshooting

### Database Issues

**Error**: `sqlite3.OperationalError: unable to open database file`

**Solution**: Ensure the `data/` directory exists:
```bash
mkdir -p data
```

### Port Already in Use

**Error**: `Address already in use`

**Solution**: Use a different port:
```bash
uv run uvicorn api.main:app --reload --port 8001
```

### Parquet Files Not Found

**Error**: `FileNotFoundError: data/cows.parquet`

**Solution**: Ensure Parquet files exist in the `data/` directory.

### Test Failures

**Error**: Tests fail during concurrent execution

**Solution**: Run tests sequentially:
```bash
uv run pytest -n 0
```

## Performance Considerations

- **Measurements Table**: Indexed on composite PK (sensor_id, cow_id, timestamp)
- **Queries**: Use date filtering to reduce result sets
- **Reports**: Generated on-demand; consider caching for frequent access
- **Database**: SQLite suitable for <1M records; migrate to PostgreSQL for larger datasets
