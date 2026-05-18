"""Tests for POST /sensors/{id} endpoint — TDD red phase."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Crea el esquema completo en una DB temporal y devuelve su ruta."""
    path = tmp_path / "test.db"
    create_schema(path)
    return path


# ---------------------------------------------------------------------------
# POST /sensors/{id}
# ---------------------------------------------------------------------------


class TestCreateSensor:
    def test_returns_201_on_success(self, client: TestClient) -> None:
        """Un POST válido debe devolver HTTP 201."""
        response = client.post("/sensors/uuid-1", json={"unit": "L"})
        assert response.status_code == 201

    def test_response_contains_all_fields(self, client: TestClient) -> None:
        """La respuesta debe incluir id y unit."""
        response = client.post("/sensors/uuid-1", json={"unit": "kg"})
        body = response.json()
        assert body["id"] == "uuid-1"
        assert body["unit"] == "kg"

    def test_sensor_persisted_in_db(self, client: TestClient, db_path: Path) -> None:
        """El sensor debe quedar almacenado en la tabla sensor."""
        client.post("/sensors/uuid-1", json={"unit": "L"})
        con = sqlite3.connect(db_path)
        row = con.execute("SELECT id, unit FROM sensor WHERE id = 'uuid-1'").fetchone()
        con.close()
        assert row is not None
        assert row[0] == "uuid-1"
        assert row[1] == "L"

    def test_duplicate_id_returns_409(self, client: TestClient) -> None:
        """Un POST con id ya existente debe devolver HTTP 409."""
        client.post("/sensors/uuid-1", json={"unit": "L"})
        response = client.post("/sensors/uuid-1", json={"unit": "kg"})
        assert response.status_code == 409

    def test_duplicate_id_error_detail(self, client: TestClient) -> None:
        """El 409 debe incluir el id conflictivo en el mensaje de error."""
        client.post("/sensors/uuid-1", json={"unit": "L"})
        response = client.post("/sensors/uuid-1", json={"unit": "L"})
        assert "uuid-1" in response.json()["detail"]

    def test_invalid_unit_returns_422(self, client: TestClient) -> None:
        """Una unidad fuera del conjunto válido {'L', 'kg'} debe devolver HTTP 422."""
        response = client.post("/sensors/uuid-1", json={"unit": "ml"})
        assert response.status_code == 422

    def test_missing_unit_returns_422(self, client: TestClient) -> None:
        """Un body sin 'unit' debe devolver HTTP 422."""
        response = client.post("/sensors/uuid-1", json={})
        assert response.status_code == 422

    def test_empty_unit_returns_422(self, client: TestClient) -> None:
        """Una 'unit' vacía debe devolver HTTP 422."""
        response = client.post("/sensors/uuid-1", json={"unit": ""})
        assert response.status_code == 422

    def test_unit_L_accepted(self, client: TestClient) -> None:
        """La unidad 'L' debe ser aceptada."""
        response = client.post("/sensors/uuid-1", json={"unit": "L"})
        assert response.status_code == 201

    def test_unit_kg_accepted(self, client: TestClient) -> None:
        """La unidad 'kg' debe ser aceptada."""
        response = client.post("/sensors/uuid-2", json={"unit": "kg"})
        assert response.status_code == 201

    def test_multiple_sensors_independent(
        self, client: TestClient, db_path: Path
    ) -> None:
        """Se pueden crear varios sensores con distintos ids."""
        client.post("/sensors/uuid-1", json={"unit": "L"})
        client.post("/sensors/uuid-2", json={"unit": "kg"})
        con = sqlite3.connect(db_path)
        count = con.execute("SELECT COUNT(*) FROM sensor").fetchone()[0]
        con.close()
        assert count == 2
