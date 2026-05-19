"""Sensor CRUD endpoints."""

import sqlite3

from fastapi import APIRouter, HTTPException

from api.dependencies import DbDep
from api.models import SensorCreate, SensorResponse

router = APIRouter(prefix="/sensors", tags=["sensors"])


@router.post("/{id}", response_model=SensorResponse, status_code=201)
async def create_sensor(id: str, sensor: SensorCreate, db: DbDep) -> SensorResponse:
    try:
        db.execute(
            "INSERT INTO sensor (id, unit) VALUES (?, ?)",
            (id, sensor.unit),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409, detail=f"A sensor with id '{id}' already exists"
        )

    return SensorResponse(id=id, unit=sensor.unit)
