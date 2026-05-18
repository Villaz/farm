"""FastAPI application — main entry point."""

import os
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Generator

import logging
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from api.models import (
    CowCreate,
    CowDetailResponse,
    CowLoadResponse,
    CowResponse,
    CowWeightSummary,
    IllCowEntry,
    LatestMeasurement,
    MeasurementCreate,
    MeasurementLoadResponse,
    MeasurementResponse,
    MilkProductionEntry,
    SensorCreate,
    SensorLoadResponse,
    SensorResponse,
)
from api.validation import VALID_UNITS

logger = logging.getLogger("fastapi")
DB_PATH = Path(os.environ.get("DATABASE_URL", "../data/cows.db"))

HEALTH_WINDOW_DAYS: int = 30
HEALTH_RECENT_DAYS: int = 3
MILK_DROP_THRESHOLD: float = 0.70
WEIGHT_LOSS_THRESHOLD: float = 0.95

app = FastAPI()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with foreign keys enabled and close it after the request."""
    logger.info("Using {DB_URL}".format(DB_URL=DB_PATH))
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
    finally:
        con.close()


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


# ---------------------------------------------------------------------------
# Cows
# ---------------------------------------------------------------------------


@app.get("/cows/{id}", response_model=CowDetailResponse)
async def get_cow(id: str, db: DbDep) -> CowDetailResponse:
    """Return the details of a cow and its latest sensor measurement.

    Args:
        id: Cow identifier.
        db: Injected SQLite connection.

    Returns:
        CowDetailResponse with cow data and latest_measurement (or None).

    Raises:
        HTTPException 404: If no cow with that id exists.
    """
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


@app.post("/cows/{id}", response_model=CowResponse, status_code=201)
async def create_cow(id: str, cow: CowCreate, db: DbDep) -> CowResponse:
    """Create a new entry in the cow table.

    Args:
        id: Unique cow identifier (path parameter).
        cow: Cow data (JSON body with name and birthdate).
        db: Injected SQLite connection.

    Returns:
        The created cow with all fields.

    Raises:
        HTTPException 409: If a cow with that id already exists.
    """
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


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


@app.post("/sensors/{id}", response_model=SensorResponse, status_code=201)
async def create_sensor(id: str, sensor: SensorCreate, db: DbDep) -> SensorResponse:
    """Create a new entry in the sensor table.

    Args:
        id: Unique sensor identifier (path parameter).
        sensor: Sensor data (JSON body with unit).
        db: Injected SQLite connection.

    Returns:
        The created sensor with all fields.

    Raises:
        HTTPException 409: If a sensor with that id already exists.
    """
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


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


@app.post("/measurements", response_model=MeasurementResponse, status_code=201)
async def create_measurement(
    measurement: MeasurementCreate, db: DbDep
) -> MeasurementResponse:
    """Create a new entry in the measurement table.

    Args:
        measurement: Measurement data (JSON body).
        db: Injected SQLite connection.

    Returns:
        The created measurement with all fields.
    """
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
            detail=f"A measurement for sensor '{measurement.sensor_id}', cow '{measurement.cow_id}' and timestamp '{ts_str}' already exists",
        )
    return MeasurementResponse(
        sensor_id=measurement.sensor_id,
        cow_id=measurement.cow_id,
        timestamp=measurement.timestamp,
        value=measurement.value,
    )


# ---------------------------------------------------------------------------
# Parquet bulk load
# ---------------------------------------------------------------------------


@app.post("/load/cows", response_model=CowLoadResponse)
async def load_cows(file: UploadFile, db: DbDep) -> CowLoadResponse:
    """Load cows from a Parquet file uploaded as multipart/form-data.

    Persists each row in the cow table, silencing duplicates. Returns the
    number of rows processed and the anomaly counters from the validation report.

    Args:
        file: Parquet file with columns id, name and birthdate.
        db: Injected SQLite connection.

    Returns:
        CowLoadResponse with rows_processed and anomaly counters.
    """
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


@app.post("/load/sensors", response_model=SensorLoadResponse)
async def load_sensors(file: UploadFile, db: DbDep) -> SensorLoadResponse:
    """Load sensors from a Parquet file uploaded as multipart/form-data.

    Persists each row with valid unit, silencing duplicates and invalid units.
    Returns rows_processed and anomaly counters.

    Args:
        file: Parquet file with columns id and unit.
        db: Injected SQLite connection.

    Returns:
        SensorLoadResponse with rows_processed and anomaly counters.
    """
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


@app.post("/load/measurements", response_model=MeasurementLoadResponse)
async def load_measurements(file: UploadFile, db: DbDep) -> MeasurementLoadResponse:
    """Load measurements from a Parquet file uploaded as multipart/form-data.

    Skips rows with null or negative values. Silences duplicates and FK violations.
    Returns rows_processed and anomaly counters from the validation report.

    Args:
        file: Parquet file with columns sensor_id, cow_id, timestamp and value.
        db: Injected SQLite connection.

    Returns:
        MeasurementLoadResponse with rows_processed and anomaly counters.
    """
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


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


@app.get("/insights/milk-production", response_model=list[MilkProductionEntry])
async def get_milk_production(db: DbDep) -> list[MilkProductionEntry]:
    """Milk production per cow per day for the last 30 days.

    Aggregates all measurements with unit 'L' for each (cow, day) combination,
    ordered by cow name and date.

    Args:
        db: Injected SQLite connection.

    Returns:
        List of entries with cow_id, cow_name, date and total_liters.
    """
    rows = db.execute(
        f"""
        SELECT c.id, c.name, DATE(m.timestamp) AS production_date, SUM(m.value) AS total_liters
          FROM measurement m
          JOIN sensor s ON s.id = m.sensor_id
          JOIN cow    c ON c.id = m.cow_id
         WHERE s.unit = 'L'
           AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
         GROUP BY c.id, c.name, DATE(m.timestamp)
        """
    ).fetchall()

    return [
        MilkProductionEntry(cow_id=r[0], cow_name=r[1], date=r[2], total_liters=r[3])
        for r in rows
    ]


@app.get("/insights/weights", response_model=list[CowWeightSummary])
async def get_weights(db: DbDep) -> list[CowWeightSummary]:
    """Current weight and 30-day average per cow.

    For each cow returns:
    - current_weight_kg: value of the most recent measurement with unit 'kg'.
    - avg_weight_30d_kg: average of 'kg' measurements from the last 30 days.

    Cows without weight measurements return null in both fields.

    Args:
        db: Injected SQLite connection.

    Returns:
        List with one entry per cow, ordered by name.
    """
    rows = db.execute(
        f"""
        SELECT
            c.id,
            c.name,
            (
                SELECT m.value
                  FROM measurement m
                  JOIN sensor s ON s.id = m.sensor_id
                 WHERE m.cow_id = c.id AND s.unit = 'kg'
                 ORDER BY m.timestamp DESC
                 LIMIT 1
            ) AS current_weight_kg,
            (
                SELECT AVG(m.value)
                  FROM measurement m
                  JOIN sensor s ON s.id = m.sensor_id
                 WHERE m.cow_id = c.id
                   AND s.unit = 'kg'
                   AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
            ) AS avg_weight_30d_kg
          FROM cow c
         ORDER BY c.name
        """
    ).fetchall()

    return [
        CowWeightSummary(
            cow_id=r[0],
            cow_name=r[1],
            current_weight_kg=r[2],
            avg_weight_30d_kg=r[3],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Health helpers — data retrieval
# ---------------------------------------------------------------------------


def _fetch_milk_recent(db: sqlite3.Connection) -> dict[str, float]:
    """Daily milk average per cow over the last HEALTH_RECENT_DAYS days."""
    return {
        r[0]: r[1]
        for r in db.execute(
            f"""
            SELECT cow_id, AVG(daily_total)
              FROM (
                    SELECT m.cow_id, DATE(m.timestamp) AS day, SUM(m.value) AS daily_total
                      FROM measurement m
                      JOIN sensor s ON s.id = m.sensor_id
                     WHERE s.unit = 'L'
                       AND m.timestamp >= datetime('now', '-{HEALTH_RECENT_DAYS} days')
                     GROUP BY m.cow_id, day
                   )
             GROUP BY cow_id
            """
        ).fetchall()
    }


def _fetch_milk_baseline(db: sqlite3.Connection) -> dict[str, float]:
    """Daily milk average per cow for the baseline window (days HEALTH_RECENT_DAYS+1 to HEALTH_WINDOW_DAYS)."""
    return {
        r[0]: r[1]
        for r in db.execute(
            f"""
            SELECT cow_id, AVG(daily_total)
              FROM (
                    SELECT m.cow_id, DATE(m.timestamp) AS day, SUM(m.value) AS daily_total
                      FROM measurement m
                      JOIN sensor s ON s.id = m.sensor_id
                     WHERE s.unit = 'L'
                       AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
                       AND m.timestamp <  datetime('now', '-{HEALTH_RECENT_DAYS} days')
                     GROUP BY m.cow_id, day
                   )
             GROUP BY cow_id
            """
        ).fetchall()
    }


def _fetch_weight_current(db: sqlite3.Connection) -> dict[str, float]:
    """Most recent weight measurement (kg) per cow."""
    return {
        r[0]: r[1]
        for r in db.execute(
            """
            SELECT cow_id, value
              FROM (
                    SELECT m.cow_id, m.value,
                           ROW_NUMBER() OVER (PARTITION BY m.cow_id ORDER BY m.timestamp DESC) AS rn
                      FROM measurement m
                      JOIN sensor s ON s.id = m.sensor_id
                     WHERE s.unit = 'kg'
                   )
             WHERE rn = 1
            """
        ).fetchall()
    }


def _fetch_weight_baseline(db: sqlite3.Connection) -> dict[str, float]:
    """Average weight (kg) per cow for the baseline window (days HEALTH_RECENT_DAYS+1 to HEALTH_WINDOW_DAYS)."""
    return {
        r[0]: r[1]
        for r in db.execute(
            f"""
            SELECT m.cow_id, AVG(m.value)
              FROM measurement m
              JOIN sensor s ON s.id = m.sensor_id
             WHERE s.unit = 'kg'
               AND m.timestamp >= datetime('now', '-{HEALTH_WINDOW_DAYS} days')
               AND m.timestamp <  datetime('now', '-{HEALTH_RECENT_DAYS} days')
             GROUP BY m.cow_id
            """
        ).fetchall()
    }


# ---------------------------------------------------------------------------
# Health helpers — business logic
# ---------------------------------------------------------------------------


def _assess_milk_health(recent: float | None, baseline: float | None) -> str | None:
    """Return a health reason if milk production dropped below MILK_DROP_THRESHOLD, else None."""
    if recent is None or baseline is None or baseline <= 0:
        return None
    if recent < MILK_DROP_THRESHOLD * baseline:
        drop_pct = round((1 - recent / baseline) * 100)
        return (
            f"Milk production drop: recent avg {recent:.1f} L/day is "
            f"{drop_pct}% below baseline {baseline:.1f} L/day"
        )
    return None


def _assess_weight_health(current_w: float | None, base_w: float | None) -> str | None:
    """Return a health reason if weight dropped below WEIGHT_LOSS_THRESHOLD, else None."""
    if current_w is None or base_w is None or base_w <= 0:
        return None
    if current_w < WEIGHT_LOSS_THRESHOLD * base_w:
        drop_pct = round((1 - current_w / base_w) * 100)
        return (
            f"Weight loss: current {current_w:.1f} kg is "
            f"{drop_pct}% below {HEALTH_WINDOW_DAYS}-day baseline {base_w:.1f} kg"
        )
    return None


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/insights/health", response_model=list[IllCowEntry])
async def get_health(db: DbDep) -> list[IllCowEntry]:
    """Detect potentially ill cows based on milk drop and weight loss indicators."""
    milk_recent = _fetch_milk_recent(db)
    milk_baseline = _fetch_milk_baseline(db)
    weight_current = _fetch_weight_current(db)
    weight_baseline = _fetch_weight_baseline(db)

    # Assess health first; collect only cow_ids that are actually ill
    ill_reasons: dict[str, list[str]] = {}
    for cow_id in set(milk_recent) | set(weight_current):
        reasons = [
            r for r in [
                _assess_milk_health(milk_recent.get(cow_id), milk_baseline.get(cow_id)),
                _assess_weight_health(weight_current.get(cow_id), weight_baseline.get(cow_id)),
            ]
            if r is not None
        ]
        if reasons:
            ill_reasons[cow_id] = reasons

    if not ill_reasons:
        return []

    # Fetch names only for the ill cows, not the entire cow table
    placeholders = ",".join("?" * len(ill_reasons))
    cow_names = {
        r[0]: r[1]
        for r in db.execute(
            f"SELECT id, name FROM cow WHERE id IN ({placeholders})",
            list(ill_reasons),
        ).fetchall()
    }

    ill_cows = [
        IllCowEntry(cow_id=cow_id, cow_name=cow_names.get(cow_id, cow_id), reasons=reasons)
        for cow_id, reasons in ill_reasons.items()
    ]
    ill_cows.sort(key=lambda e: e.cow_name)
    return ill_cows


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@app.get("/reports/daily", response_class=PlainTextResponse)
async def get_daily_report(db: DbDep, date: date | None = None) -> str:
    """Generate and return the daily farm report in plain text format.

    Includes three sections for the specified date (or today if omitted):
    milk production, weight summary and health alerts.

    Args:
        db: Injected SQLite connection.
        date: Target date for the report (YYYY-MM-DD). Default: today (UTC).

    Returns:
        Plain text report with three sections.
    """
    from api.report import ReportGenerator

    target = date or datetime.utcnow().date()
    return ReportGenerator().generate_from_connection(db, target)


# ---------------------------------------------------------------------------
# Health check endpoint
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check() -> dict:
    """Simple health check endpoint for container orchestration."""
    return {"status": "ok"}
