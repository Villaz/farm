"""Tests for POST /measurements endpoint — TDD red phase."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_schema

SENSOR_ID = "sensor-uuid-1"
COW_ID = "cow-uuid-1"

VALID_PAYLOAD = {
    "sensor_id": SENSOR_ID,
    "cow_id": COW_ID,
    "timestamp": "2023-06-15T10:00:00",
    "value": 4.72,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Crea el esquema completo (cow, sensor, measurement) con datos de referencia."""
    path = tmp_path / "test.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO cow VALUES (?, ?, ?)", (COW_ID, "Bessie #1", "2020-01-01"))
    con.execute("INSERT INTO sensor VALUES (?, ?)", (SENSOR_ID, "L"))
    con.commit()
    con.close()
    return path


# ---------------------------------------------------------------------------
# POST /measurements
# ---------------------------------------------------------------------------


class TestCreateMeasurement:
    def test_returns_201_on_success(self, client: TestClient) -> None:
        """Un POST válido debe devolver HTTP 201."""
        response = client.post("/measurements", json=VALID_PAYLOAD)
        assert response.status_code == 201

    def test_response_contains_all_fields(self, client: TestClient) -> None:
        """La respuesta debe incluir sensor_id, cow_id, timestamp y value."""
        response = client.post("/measurements", json=VALID_PAYLOAD)
        body = response.json()
        assert body["sensor_id"] == VALID_PAYLOAD["sensor_id"]
        assert body["cow_id"] == VALID_PAYLOAD["cow_id"]
        assert body["value"] == VALID_PAYLOAD["value"]
        assert "timestamp" in body

    def test_measurement_persisted_in_db(
        self, client: TestClient, db_path: Path
    ) -> None:
        """La medición debe quedar almacenada en la tabla measurement."""
        client.post("/measurements", json=VALID_PAYLOAD)
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT sensor_id, cow_id, value FROM measurement WHERE sensor_id = ?",
            (VALID_PAYLOAD["sensor_id"],),
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == VALID_PAYLOAD["sensor_id"]
        assert row[1] == VALID_PAYLOAD["cow_id"]
        assert row[2] == VALID_PAYLOAD["value"]

    def test_multiple_measurements_different_timestamp(
        self, client: TestClient, db_path: Path
    ) -> None:
        """Se pueden insertar varias mediciones del mismo sensor+vaca con distinto timestamp."""
        client.post("/measurements", json=VALID_PAYLOAD)
        client.post(
            "/measurements", json={**VALID_PAYLOAD, "timestamp": "2023-06-16T10:00:00"}
        )
        con = sqlite3.connect(db_path)
        count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        con.close()
        assert count == 2

    def test_duplicate_pk_returns_409(self, client: TestClient) -> None:
        """Una PK (sensor_id, cow_id, timestamp) duplicada debe devolver HTTP 409."""
        client.post("/measurements", json=VALID_PAYLOAD)
        response = client.post("/measurements", json=VALID_PAYLOAD)
        assert response.status_code == 409

    def test_unknown_sensor_id_returns_422(self, client: TestClient) -> None:
        """Un sensor_id que no existe en la tabla sensor debe devolver HTTP 422."""
        payload = {**VALID_PAYLOAD, "sensor_id": "sensor-no-existe"}
        response = client.post("/measurements", json=payload)
        assert response.status_code == 422

    def test_unknown_cow_id_returns_422(self, client: TestClient) -> None:
        """Un cow_id que no existe en la tabla cow debe devolver HTTP 422."""
        payload = {**VALID_PAYLOAD, "cow_id": "cow-no-existe"}
        response = client.post("/measurements", json=payload)
        assert response.status_code == 422

    def test_negative_value_returns_422(self, client: TestClient) -> None:
        """Un value negativo debe devolver HTTP 422."""
        response = client.post("/measurements", json={**VALID_PAYLOAD, "value": -1.0})
        assert response.status_code == 422

    def test_zero_value_accepted(self, client: TestClient) -> None:
        """Un value igual a 0 debe ser aceptado."""
        response = client.post("/measurements", json={**VALID_PAYLOAD, "value": 0.0})
        assert response.status_code == 201

    def test_missing_sensor_id_returns_422(self, client: TestClient) -> None:
        """Un body sin 'sensor_id' debe devolver HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "sensor_id"}
        assert client.post("/measurements", json=payload).status_code == 422

    def test_missing_cow_id_returns_422(self, client: TestClient) -> None:
        """Un body sin 'cow_id' debe devolver HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "cow_id"}
        assert client.post("/measurements", json=payload).status_code == 422

    def test_missing_timestamp_returns_422(self, client: TestClient) -> None:
        """Un body sin 'timestamp' debe devolver HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "timestamp"}
        assert client.post("/measurements", json=payload).status_code == 422

    def test_missing_value_returns_422(self, client: TestClient) -> None:
        """Un body sin 'value' debe devolver HTTP 422."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "value"}
        assert client.post("/measurements", json=payload).status_code == 422

    def test_invalid_timestamp_returns_422(self, client: TestClient) -> None:
        """Un timestamp con formato inválido debe devolver HTTP 422."""
        response = client.post(
            "/measurements", json={**VALID_PAYLOAD, "timestamp": "not-a-date"}
        )
        assert response.status_code == 422

    def test_timestamp_stored_as_iso_string(
        self, client: TestClient, db_path: Path
    ) -> None:
        """El timestamp debe almacenarse como cadena ISO compatible con el loader."""
        client.post(
            "/measurements", json={**VALID_PAYLOAD, "timestamp": "2023-06-15T10:00:00"}
        )
        con = sqlite3.connect(db_path)
        ts = con.execute("SELECT timestamp FROM measurement LIMIT 1").fetchone()[0]
        con.close()
        assert "2023-06-15" in ts

    def test_empty_sensor_id_returns_422(self, client: TestClient) -> None:
        """Un sensor_id vacío debe devolver HTTP 422."""
        response = client.post("/measurements", json={**VALID_PAYLOAD, "sensor_id": ""})
        assert response.status_code == 422

    def test_sensor_id_too_long_returns_422(self, client: TestClient) -> None:
        """Un sensor_id de más de 255 caracteres debe devolver HTTP 422."""
        response = client.post(
            "/measurements", json={**VALID_PAYLOAD, "sensor_id": "s" * 256}
        )
        assert response.status_code == 422

    def test_empty_cow_id_returns_422(self, client: TestClient) -> None:
        """Un cow_id vacío debe devolver HTTP 422."""
        response = client.post("/measurements", json={**VALID_PAYLOAD, "cow_id": ""})
        assert response.status_code == 422

    def test_cow_id_too_long_returns_422(self, client: TestClient) -> None:
        """Un cow_id de más de 255 caracteres debe devolver HTTP 422."""
        response = client.post(
            "/measurements", json={**VALID_PAYLOAD, "cow_id": "c" * 256}
        )
        assert response.status_code == 422
