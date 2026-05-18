"""Shared fixtures and helpers for all test modules."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_db

SCHEMA = """
CREATE TABLE cow (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    birthdate TEXT NOT NULL
);
CREATE TABLE sensor (
    id   TEXT PRIMARY KEY,
    unit TEXT NOT NULL
);
CREATE TABLE measurement (
    sensor_id TEXT      NOT NULL,
    cow_id    TEXT      NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    value     REAL,
    PRIMARY KEY (sensor_id, cow_id, timestamp),
    FOREIGN KEY (sensor_id) REFERENCES sensor(id),
    FOREIGN KEY (cow_id)    REFERENCES cow(id)
);
"""


def create_schema(path: Path) -> None:
    """Create the three-table schema (cow, sensor, measurement) in a SQLite DB."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.close()


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient with get_db overridden to point at the test database."""

    def override_get_db():
        con = sqlite3.connect(db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
        finally:
            con.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
