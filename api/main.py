"""FastAPI application — main entry point."""

import logging

from fastapi import FastAPI

from api.dependencies import get_db  # re-exported so tests can do: from api.main import get_db
from api.routers import cows, insights, load, measurements, reports, sensors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI()

app.include_router(cows.router)
app.include_router(sensors.router)
app.include_router(measurements.router)
app.include_router(load.router)
app.include_router(insights.router)
app.include_router(reports.router)


@app.get("/health")
def health_check() -> dict:
    """Simple health check endpoint for container orchestration."""
    return {"status": "ok"}
