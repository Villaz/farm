"""Parquet bulk-load endpoints."""

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, UploadFile

from api.dependencies import DbDep
from api.models import CowLoadResponse, MeasurementLoadResponse, SensorLoadResponse
from api.validation import VALID_UNITS

router = APIRouter(prefix="/load", tags=["load"])


@router.post("/cows", response_model=CowLoadResponse)
async def load_cows(file: UploadFile, db: DbDep) -> CowLoadResponse:
    from loader import CowLoader

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        loader = CowLoader(parquet_path=tmp_path)
        df = loader.load()
        report = loader.validate(df)
        for _, row in df.iterrows():
            try:
                db.execute(
                    "INSERT INTO cow (id, name, birthdate) VALUES (?, ?, ?)",
                    (
                        row["id"],
                        row["name"],
                        str(pd.Timestamp(row["birthdate"]).date()),
                    ),
                )
            except sqlite3.IntegrityError:
                pass
        db.commit()
    finally:
        tmp_path.unlink(missing_ok=True)

    return CowLoadResponse(
        rows_processed=len(df),
        duplicate_names=len(report.duplicate_names),
        duplicate_ids=len(report.duplicate_ids),
        future_birthdates=len(report.future_birthdates),
    )


@router.post("/sensors", response_model=SensorLoadResponse)
async def load_sensors(file: UploadFile, db: DbDep) -> SensorLoadResponse:
    from loader import SensorLoader

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        loader = SensorLoader(parquet_path=tmp_path)
        df = loader.load()
        report = loader.validate(df)
        for _, row in df.iterrows():
            if (
                pd.isna(row["id"])
                or pd.isna(row["unit"])
                or row["unit"] not in VALID_UNITS
            ):
                continue
            try:
                db.execute(
                    "INSERT INTO sensor (id, unit) VALUES (?, ?)",
                    (row["id"], row["unit"]),
                )
            except sqlite3.IntegrityError:
                pass
        db.commit()
    finally:
        tmp_path.unlink(missing_ok=True)

    return SensorLoadResponse(
        rows_processed=len(df),
        null_values=len(report.null_values),
        duplicate_ids=len(report.duplicate_ids),
        unknown_units=len(report.unknown_units),
    )


@router.post("/measurements", response_model=MeasurementLoadResponse)
async def load_measurements(file: UploadFile, db: DbDep) -> MeasurementLoadResponse:
    from loader import MeasurementLoader

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        loader = MeasurementLoader(parquet_path=tmp_path)
        df = loader.load()
        report = loader.validate(df)
        db.execute("PRAGMA foreign_keys = ON")
        for _, row in df.iterrows():
            if pd.isna(row["value"]) or row["value"] < 0:
                continue
            ts_str = pd.Timestamp(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            try:
                db.execute(
                    "INSERT INTO measurement (sensor_id, cow_id, timestamp, value) VALUES (?, ?, ?, ?)",
                    (
                        str(row["sensor_id"]),
                        str(row["cow_id"]),
                        ts_str,
                        float(row["value"]),
                    ),
                )
            except sqlite3.IntegrityError:
                pass
        db.commit()
    finally:
        tmp_path.unlink(missing_ok=True)

    return MeasurementLoadResponse(
        rows_processed=len(df),
        null_values=len(report.null_values),
        negative_values=len(report.negative_values),
        future_timestamps=len(report.future_timestamps),
    )
