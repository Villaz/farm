"""Measurement endpoints."""

import sqlite3

from fastapi import APIRouter, HTTPException

from api.dependencies import DbDep
from api.models import MeasurementCreate, MeasurementResponse

router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.post("", response_model=MeasurementResponse, status_code=201)
async def create_measurement(
    measurement: MeasurementCreate, db: DbDep
) -> MeasurementResponse:
    ts_str = measurement.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute(
            "INSERT INTO measurement (sensor_id, cow_id, timestamp, value) VALUES (?, ?, ?, ?)",
            (measurement.sensor_id, measurement.cow_id, ts_str, measurement.value),
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        if "FOREIGN KEY" in str(e):
            raise HTTPException(
                status_code=422, detail="sensor_id or cow_id does not exist in database"
            )
        raise HTTPException(
            status_code=409,
            detail=(
                f"A measurement for sensor '{measurement.sensor_id}', "
                f"cow '{measurement.cow_id}' and timestamp '{ts_str}' already exists"
            ),
        )

    return MeasurementResponse(
        sensor_id=measurement.sensor_id,
        cow_id=measurement.cow_id,
        timestamp=measurement.timestamp,
        value=measurement.value,
    )
