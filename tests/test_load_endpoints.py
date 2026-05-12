"""Tests for parquet load endpoints — TDD red phase."""

import io
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app, get_db
from init_db import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    def override_get_db():
        con = sqlite3.connect(db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
        finally:
            con.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def cows_parquet() -> bytes:
    df = pd.DataFrame(
        {
            "id": ["c-1", "c-2", "c-3"],
            "name": ["Bessie", "Daisy", "Molly"],
            "birthdate": pd.to_datetime(["2020-01-01", "2021-03-15", "2019-07-20"]),
        }
    )
    return _to_parquet_bytes(df)


@pytest.fixture()
def sensors_parquet() -> bytes:
    df = pd.DataFrame(
        {
            "id": ["s-1", "s-2"],
            "unit": ["L", "kg"],
        }
    )
    return _to_parquet_bytes(df)


@pytest.fixture()
def measurements_parquet(db_path: Path) -> bytes:
    """Dataset de mediciones con sensores y vacas prerregistrados en la DB."""
    with sqlite3.connect(db_path) as con:
        con.execute("INSERT INTO sensor VALUES ('s-1', 'L')")
        con.execute("INSERT INTO sensor VALUES ('s-2', 'kg')")
        con.execute("INSERT INTO cow VALUES ('c-1', 'Bessie', '2020-01-01')")
        con.execute("INSERT INTO cow VALUES ('c-2', 'Daisy', '2021-03-15')")
    df = pd.DataFrame(
        {
            "sensor_id": ["s-1", "s-2"],
            "cow_id": ["c-1", "c-2"],
            "timestamp": [
                pd.Timestamp("2023-01-01 10:00:00"),
                pd.Timestamp("2023-01-02 12:00:00"),
            ],
            "value": [4.72, 549.51],
        }
    )
    return _to_parquet_bytes(df)


# ---------------------------------------------------------------------------
# POST /load/cows
# ---------------------------------------------------------------------------


class TestLoadCows:
    def test_returns_200(self, client: TestClient, cows_parquet: bytes) -> None:
        """El endpoint debe responder 200 con un parquet válido."""
        r = client.post(
            "/load/cows",
            files={"file": ("cows.parquet", cows_parquet, "application/octet-stream")},
        )
        assert r.status_code == 200

    def test_response_has_rows_processed(
        self, client: TestClient, cows_parquet: bytes
    ) -> None:
        """La respuesta debe incluir el número de filas procesadas."""
        r = client.post(
            "/load/cows",
            files={"file": ("cows.parquet", cows_parquet, "application/octet-stream")},
        )
        assert "rows_processed" in r.json()
        assert r.json()["rows_processed"] == 3

    def test_inserts_rows_into_db(
        self, client: TestClient, cows_parquet: bytes, db_path: Path
    ) -> None:
        """Las vacas del parquet deben quedar persistidas en la tabla cow."""
        client.post(
            "/load/cows",
            files={"file": ("cows.parquet", cows_parquet, "application/octet-stream")},
        )
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM cow").fetchone()[0]
        assert count == 3

    def test_duplicate_load_does_not_raise(
        self, client: TestClient, cows_parquet: bytes
    ) -> None:
        """Cargar el mismo parquet dos veces no debe devolver error."""
        client.post(
            "/load/cows",
            files={"file": ("cows.parquet", cows_parquet, "application/octet-stream")},
        )
        r = client.post(
            "/load/cows",
            files={"file": ("cows.parquet", cows_parquet, "application/octet-stream")},
        )
        assert r.status_code == 200

    def test_duplicate_load_does_not_duplicate_rows(
        self, client: TestClient, cows_parquet: bytes, db_path: Path
    ) -> None:
        """Cargar el mismo parquet dos veces no debe duplicar filas."""
        for _ in range(2):
            client.post(
                "/load/cows",
                files={
                    "file": ("cows.parquet", cows_parquet, "application/octet-stream")
                },
            )
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM cow").fetchone()[0]
        assert count == 3

    def test_response_contains_anomaly_counts(
        self, client: TestClient, cows_parquet: bytes
    ) -> None:
        """La respuesta debe incluir contadores de anomalías del informe de validación."""
        r = client.post(
            "/load/cows",
            files={"file": ("cows.parquet", cows_parquet, "application/octet-stream")},
        )
        body = r.json()
        assert "duplicate_names" in body
        assert "duplicate_ids" in body
        assert "future_birthdates" in body

    def test_future_birthdates_reported(
        self, client: TestClient, db_path: Path
    ) -> None:
        """El campo future_birthdates debe reflejar vacas con fecha de nacimiento futura."""
        df = pd.DataFrame(
            {
                "id": ["c-future"],
                "name": ["FutureCow"],
                "birthdate": pd.to_datetime(["2099-01-01"]),
            }
        )
        r = client.post(
            "/load/cows",
            files={
                "file": (
                    "cows.parquet",
                    _to_parquet_bytes(df),
                    "application/octet-stream",
                )
            },
        )
        assert r.json()["future_birthdates"] == 1


# ---------------------------------------------------------------------------
# POST /load/sensors
# ---------------------------------------------------------------------------


class TestLoadSensors:
    def test_returns_200(self, client: TestClient, sensors_parquet: bytes) -> None:
        """El endpoint debe responder 200 con un parquet válido."""
        r = client.post(
            "/load/sensors",
            files={
                "file": ("sensors.parquet", sensors_parquet, "application/octet-stream")
            },
        )
        assert r.status_code == 200

    def test_response_has_rows_processed(
        self, client: TestClient, sensors_parquet: bytes
    ) -> None:
        """La respuesta debe incluir el número de filas procesadas."""
        r = client.post(
            "/load/sensors",
            files={
                "file": ("sensors.parquet", sensors_parquet, "application/octet-stream")
            },
        )
        assert r.json()["rows_processed"] == 2

    def test_inserts_rows_into_db(
        self, client: TestClient, sensors_parquet: bytes, db_path: Path
    ) -> None:
        """Los sensores del parquet deben quedar persistidos en la tabla sensor."""
        client.post(
            "/load/sensors",
            files={
                "file": ("sensors.parquet", sensors_parquet, "application/octet-stream")
            },
        )
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM sensor").fetchone()[0]
        assert count == 2

    def test_invalid_unit_not_stored(self, client: TestClient, db_path: Path) -> None:
        """Sensores con unidades inválidas no deben persistirse."""
        df = pd.DataFrame(
            {
                "id": ["s-bad"],
                "unit": ["ml"],
            }
        )
        client.post(
            "/load/sensors",
            files={
                "file": (
                    "sensors.parquet",
                    _to_parquet_bytes(df),
                    "application/octet-stream",
                )
            },
        )
        with sqlite3.connect(db_path) as con:
            count = con.execute(
                "SELECT COUNT(*) FROM sensor WHERE id='s-bad'"
            ).fetchone()[0]
        assert count == 0

    def test_response_contains_anomaly_counts(
        self, client: TestClient, sensors_parquet: bytes
    ) -> None:
        """La respuesta debe incluir contadores de anomalías."""
        r = client.post(
            "/load/sensors",
            files={
                "file": ("sensors.parquet", sensors_parquet, "application/octet-stream")
            },
        )
        body = r.json()
        assert "null_values" in body
        assert "duplicate_ids" in body
        assert "unknown_units" in body

    def test_unknown_units_reported(self, client: TestClient) -> None:
        """El campo unknown_units debe contar sensores con unidades desconocidas."""
        df = pd.DataFrame({"id": ["s-bad"], "unit": ["oz"]})
        r = client.post(
            "/load/sensors",
            files={
                "file": (
                    "sensors.parquet",
                    _to_parquet_bytes(df),
                    "application/octet-stream",
                )
            },
        )
        assert r.json()["unknown_units"] == 1

    def test_duplicate_load_does_not_duplicate_rows(
        self, client: TestClient, sensors_parquet: bytes, db_path: Path
    ) -> None:
        """Cargar el mismo parquet dos veces no debe duplicar filas."""
        for _ in range(2):
            client.post(
                "/load/sensors",
                files={
                    "file": (
                        "sensors.parquet",
                        sensors_parquet,
                        "application/octet-stream",
                    )
                },
            )
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM sensor").fetchone()[0]
        assert count == 2


# ---------------------------------------------------------------------------
# POST /load/measurements
# ---------------------------------------------------------------------------


class TestLoadMeasurements:
    def test_returns_200(self, client: TestClient, measurements_parquet: bytes) -> None:
        """El endpoint debe responder 200 con un parquet válido."""
        r = client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    measurements_parquet,
                    "application/octet-stream",
                )
            },
        )
        assert r.status_code == 200

    def test_response_has_rows_processed(
        self, client: TestClient, measurements_parquet: bytes
    ) -> None:
        """La respuesta debe incluir el número de filas procesadas."""
        r = client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    measurements_parquet,
                    "application/octet-stream",
                )
            },
        )
        assert r.json()["rows_processed"] == 2

    def test_inserts_rows_into_db(
        self, client: TestClient, measurements_parquet: bytes, db_path: Path
    ) -> None:
        """Las mediciones del parquet deben quedar persistidas en la tabla measurement."""
        client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    measurements_parquet,
                    "application/octet-stream",
                )
            },
        )
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == 2

    def test_null_values_skipped(self, client: TestClient, db_path: Path) -> None:
        """Filas con value nulo deben omitirse y no persistirse."""
        with sqlite3.connect(db_path) as con:
            con.execute("INSERT INTO sensor VALUES ('s-1', 'L')")
            con.execute("INSERT INTO cow VALUES ('c-1', 'Bessie', '2020-01-01')")
        df = pd.DataFrame(
            {
                "sensor_id": ["s-1"],
                "cow_id": ["c-1"],
                "timestamp": [pd.Timestamp("2023-01-01 10:00:00")],
                "value": [None],
            }
        )
        r = client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    _to_parquet_bytes(df),
                    "application/octet-stream",
                )
            },
        )
        assert r.status_code == 200
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == 0

    def test_negative_values_skipped(self, client: TestClient, db_path: Path) -> None:
        """Filas con value negativo deben omitirse y no persistirse."""
        with sqlite3.connect(db_path) as con:
            con.execute("INSERT INTO sensor VALUES ('s-1', 'L')")
            con.execute("INSERT INTO cow VALUES ('c-1', 'Bessie', '2020-01-01')")
        df = pd.DataFrame(
            {
                "sensor_id": ["s-1"],
                "cow_id": ["c-1"],
                "timestamp": [pd.Timestamp("2023-01-01 10:00:00")],
                "value": [-5.0],
            }
        )
        r = client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    _to_parquet_bytes(df),
                    "application/octet-stream",
                )
            },
        )
        assert r.status_code == 200
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == 0

    def test_invalid_foreign_key_skipped(
        self, client: TestClient, db_path: Path
    ) -> None:
        """Mediciones con sensor_id o cow_id inexistentes deben omitirse."""
        df = pd.DataFrame(
            {
                "sensor_id": ["no-existe"],
                "cow_id": ["tampoco"],
                "timestamp": [pd.Timestamp("2023-01-01 10:00:00")],
                "value": [1.0],
            }
        )
        r = client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    _to_parquet_bytes(df),
                    "application/octet-stream",
                )
            },
        )
        assert r.status_code == 200
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == 0

    def test_response_contains_anomaly_counts(
        self, client: TestClient, measurements_parquet: bytes
    ) -> None:
        """La respuesta debe incluir contadores de anomalías."""
        r = client.post(
            "/load/measurements",
            files={
                "file": (
                    "measurements.parquet",
                    measurements_parquet,
                    "application/octet-stream",
                )
            },
        )
        body = r.json()
        assert "null_values" in body
        assert "negative_values" in body
        assert "future_timestamps" in body

    def test_duplicate_load_does_not_duplicate_rows(
        self, client: TestClient, measurements_parquet: bytes, db_path: Path
    ) -> None:
        """Cargar el mismo parquet dos veces no debe duplicar filas."""
        for _ in range(2):
            client.post(
                "/load/measurements",
                files={
                    "file": (
                        "measurements.parquet",
                        measurements_parquet,
                        "application/octet-stream",
                    )
                },
            )
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == 2
