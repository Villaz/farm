"""SQLite database schema initialization.

Creates the cow, sensor and measurement tables with their primary key and
foreign key constraints. The operation is idempotent: it can be executed
on an already existing database without deleting data or raising errors.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS cow (
    id        TEXT NOT NULL,
    name      TEXT NOT NULL,
    birthdate TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS sensor (
    id   TEXT NOT NULL,
    unit TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS measurement (
    sensor_id TEXT      NOT NULL,
    cow_id    TEXT      NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    value     REAL,
    PRIMARY KEY (sensor_id, cow_id, timestamp),
    FOREIGN KEY (sensor_id) REFERENCES sensor(id),
    FOREIGN KEY (cow_id)    REFERENCES cow(id)
);

-- Accelerates queries that filter/sort measurements by cow and time
-- (e.g. latest measurement per cow, 30-day health windows).
CREATE INDEX IF NOT EXISTS idx_measurement_cow_ts
    ON measurement(cow_id, timestamp DESC);

-- Accelerates JOINs between measurement and sensor when filtering by sensor.
CREATE INDEX IF NOT EXISTS idx_measurement_sensor_ts
    ON measurement(sensor_id, timestamp);

-- Accelerates WHERE s.unit = 'L' / 'kg' filters present in every insight query.
CREATE INDEX IF NOT EXISTS idx_sensor_unit
    ON sensor(unit);
"""


def init_db(db_path: str | Path = "cows.db") -> None:
    """Create the complete database schema if it does not exist.

    Execute the DDL for the cow, sensor and measurement tables. Uses
    CREATE TABLE IF NOT EXISTS, so it can be called multiple times
    without side effects on existing data.

    Args:
        db_path: Path to the SQLite file. Created if it does not exist.
    """
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as con:
        con.executescript(_DDL)
    logger.info("Database initialized at '%s'.", db_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    print("Database 'cows.db' initialized successfully.")
