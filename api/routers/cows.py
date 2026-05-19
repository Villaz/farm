"""Cow CRUD endpoints."""

import sqlite3

from fastapi import APIRouter, HTTPException

from api.dependencies import DbDep
from api.models import CowCreate, CowDetailResponse, CowResponse, LatestMeasurement

router = APIRouter(prefix="/cows", tags=["cows"])


@router.get("/{id}", response_model=CowDetailResponse)
async def get_cow(id: str, db: DbDep) -> CowDetailResponse:
    row = db.execute(
        "SELECT id, name, birthdate FROM cow WHERE id = ?", (id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Cow '{id}' not found")

    m_row = db.execute(
        """
        SELECT m.sensor_id, m.timestamp, m.value, s.unit
          FROM measurement m
          JOIN sensor s ON s.id = m.sensor_id
         WHERE m.cow_id = ?
         ORDER BY m.timestamp DESC
         LIMIT 1
        """,
        (id,),
    ).fetchone()

    latest = (
        LatestMeasurement(
            sensor_id=m_row[0],
            timestamp=m_row[1],
            value=m_row[2],
            unit=m_row[3],
        )
        if m_row
        else None
    )

    return CowDetailResponse(
        id=row[0], name=row[1], birthdate=row[2], latest_measurement=latest
    )


@router.post("/{id}", response_model=CowResponse, status_code=201)
async def create_cow(id: str, cow: CowCreate, db: DbDep) -> CowResponse:
    try:
        db.execute(
            "INSERT INTO cow (id, name, birthdate) VALUES (?, ?, ?)",
            (id, cow.name, str(cow.birthdate)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409, detail=f"A cow with id '{id}' already exists"
        )

    return CowResponse(id=id, name=cow.name, birthdate=cow.birthdate)
