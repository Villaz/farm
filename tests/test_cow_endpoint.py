"""Tests for /cows/{id} endpoints (POST and GET) - TDD approach."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_schema

COW_ID = "cow-uuid-1"
SENSOR_ID = "sensor-uuid-1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Database with cow, sensor and measurement tables for POST and GET tests."""
    path = tmp_path / "test.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO cow    VALUES ('cow-uuid-1', 'Bessie #1', '2020-03-15');
        INSERT INTO cow    VALUES ('cow-uuid-2', 'Daisy #2',  '2021-06-10');
        INSERT INTO sensor VALUES ('sensor-uuid-1', 'L');
        INSERT INTO sensor VALUES ('sensor-uuid-2', 'kg');
    """)
    con.commit()
    con.close()
    return path


def _insert_measurement(
    db_path: Path, sensor_id: str, cow_id: str, timestamp: str, value: float
) -> None:
    """Helper function to insert measurements in the test database."""
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO measurement VALUES (?, ?, ?, ?)",
        (sensor_id, cow_id, timestamp, value),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# POST /cows/{id}
# ---------------------------------------------------------------------------


class TestCreateCow:
    """Tests for the POST /cows/{id} endpoint."""

    def test_returns_201_on_success(self, client: TestClient) -> None:
        """Valid POST request should return HTTP 201."""
        response = client.post(
            "/cows/uuid-1",
            json={"name": "Bessie #1", "birthdate": "2020-03-15"},
        )
        assert response.status_code == 201

    def test_response_contains_all_fields(self, client: TestClient) -> None:
        """Response should include id, name and birthdate."""
        response = client.post(
            "/cows/uuid-1",
            json={"name": "Bessie #1", "birthdate": "2020-03-15"},
        )
        body = response.json()
        assert body["id"] == "uuid-1"
        assert body["name"] == "Bessie #1"
        assert body["birthdate"] == "2020-03-15"

    def test_cow_persisted_in_db(self, client: TestClient, db_path: Path) -> None:
        """Cow should be stored in the cow table."""
        client.post(
            "/cows/uuid-1",
            json={"name": "Bessie #1", "birthdate": "2020-03-15"},
        )
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT id, name, birthdate FROM cow WHERE id = 'uuid-1'"
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == "uuid-1"
        assert row[1] == "Bessie #1"

    def test_duplicate_id_returns_409(self, client: TestClient) -> None:
        """POST with existing id should return HTTP 409."""
        payload = {"name": "Bessie #1", "birthdate": "2020-03-15"}
        client.post("/cows/uuid-1", json=payload)
        response = client.post("/cows/uuid-1", json=payload)
        assert response.status_code == 409

    def test_duplicate_id_error_detail(self, client: TestClient) -> None:
        """HTTP 409 should include the conflicting id in the error message."""
        payload = {"name": "Bessie #1", "birthdate": "2020-03-15"}
        client.post("/cows/uuid-1", json=payload)
        response = client.post("/cows/uuid-1", json=payload)
        assert "uuid-1" in response.json()["detail"]

    def test_missing_name_returns_422(self, client: TestClient) -> None:
        """Request body without 'name' should return HTTP 422."""
        response = client.post("/cows/uuid-1", json={"birthdate": "2020-03-15"})
        assert response.status_code == 422

    def test_missing_birthdate_returns_422(self, client: TestClient) -> None:
        """Request body without 'birthdate' should return HTTP 422."""
        response = client.post("/cows/uuid-1", json={"name": "Bessie #1"})
        assert response.status_code == 422

    def test_invalid_date_format_returns_422(self, client: TestClient) -> None:
        """Date with invalid format should return HTTP 422."""
        response = client.post(
            "/cows/uuid-1",
            json={"name": "Bessie #1", "birthdate": "not-a-date"},
        )
        assert response.status_code == 422

    def test_empty_body_returns_422(self, client: TestClient) -> None:
        """Empty request body should return HTTP 422."""
        response = client.post("/cows/uuid-1", json={})
        assert response.status_code == 422

    def test_multiple_cows_independent(self, client: TestClient, db_path: Path) -> None:
        """Multiple cows can be created with different ids."""
        client.post(
            "/cows/uuid-1", json={"name": "Bessie #1", "birthdate": "2020-01-01"}
        )
        client.post(
            "/cows/uuid-2", json={"name": "Daisy #2", "birthdate": "2021-06-15"}
        )
        con = sqlite3.connect(db_path)
        count = con.execute("SELECT COUNT(*) FROM cow").fetchone()[0]
        con.close()
        assert count == 4  # 2 pre-populated + 2 new


# ---------------------------------------------------------------------------
# GET /cows/{id}
# ---------------------------------------------------------------------------


class TestGetCow:
    """Tests for the GET /cows/{id} endpoint."""

    def test_returns_200_for_existing_cow(self, client: TestClient) -> None:
        """Existing cow should return HTTP 200."""
        assert client.get(f"/cows/{COW_ID}").status_code == 200

    def test_returns_404_for_unknown_cow(self, client: TestClient) -> None:
        """Non-existent cow should return HTTP 404."""
        assert client.get("/cows/no-existe").status_code == 404

    def test_response_contains_cow_fields(self, client: TestClient) -> None:
        """Response should include cow id, name and birthdate."""
        body = client.get(f"/cows/{COW_ID}").json()
        assert body["id"] == COW_ID
        assert body["name"] == "Bessie #1"
        assert body["birthdate"] == "2020-03-15"

    def test_latest_measurement_none_when_no_data(self, client: TestClient) -> None:
        """When no measurements exist, latest_measurement should be null."""
        body = client.get(f"/cows/{COW_ID}").json()
        assert body["latest_measurement"] is None

    def test_latest_measurement_populated(
        self, client: TestClient, db_path: Path
    ) -> None:
        """When measurements exist, latest_measurement should contain most recent record data."""
        _insert_measurement(db_path, SENSOR_ID, COW_ID, "2023-06-15 10:00:00", 4.72)
        body = client.get(f"/cows/{COW_ID}").json()
        m = body["latest_measurement"]
        assert m is not None
        assert m["sensor_id"] == SENSOR_ID
        assert m["value"] == 4.72
        assert "2023-06-15" in m["timestamp"]

    def test_latest_measurement_is_most_recent(
        self, client: TestClient, db_path: Path
    ) -> None:
        """With multiple measurements, the most recent timestamp should be returned."""
        _insert_measurement(db_path, SENSOR_ID, COW_ID, "2022-01-01 08:00:00", 3.0)
        _insert_measurement(db_path, SENSOR_ID, COW_ID, "2023-06-15 10:00:00", 4.72)
        _insert_measurement(db_path, SENSOR_ID, COW_ID, "2021-05-20 12:00:00", 5.0)
        body = client.get(f"/cows/{COW_ID}").json()
        assert body["latest_measurement"]["value"] == 4.72

    def test_latest_measurement_includes_sensor_unit(
        self, client: TestClient, db_path: Path
    ) -> None:
        """latest_measurement should include the corresponding sensor unit."""
        _insert_measurement(db_path, SENSOR_ID, COW_ID, "2023-06-15 10:00:00", 4.72)
        body = client.get(f"/cows/{COW_ID}").json()
        assert body["latest_measurement"]["unit"] == "L"

    def test_latest_measurement_from_any_sensor(
        self, client: TestClient, db_path: Path
    ) -> None:
        """Latest measurement can come from any sensor."""
        _insert_measurement(
            db_path, "sensor-uuid-1", COW_ID, "2022-01-01 08:00:00", 3.0
        )
        _insert_measurement(
            db_path, "sensor-uuid-2", COW_ID, "2023-06-15 10:00:00", 520.0
        )
        body = client.get(f"/cows/{COW_ID}").json()
        m = body["latest_measurement"]
        assert m["sensor_id"] == "sensor-uuid-2"
        assert m["unit"] == "kg"

    def test_measurements_from_other_cows_ignored(
        self, client: TestClient, db_path: Path
    ) -> None:
        """Measurements from other cows should not appear in the response."""
        _insert_measurement(
            db_path, SENSOR_ID, "cow-uuid-2", "2024-01-01 08:00:00", 9.99
        )
        body = client.get(f"/cows/{COW_ID}").json()
        assert body["latest_measurement"] is None
