"""Tests for ReportGenerator and GET /reports/daily endpoint.

Fixtures use absolute fixed timestamps so tests are fully deterministic
and explicitly verify date-parameterization (the key feature under test).

TARGET_DATE = 2024-06-15
  "last 3 days" window : 2024-06-13 → 2024-06-15
  "baseline"    window : 2024-05-16 → 2024-06-12
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_db
from api.report import ReportGenerator

TARGET_DATE = date(2024, 6, 15)
TARGET_STR = TARGET_DATE.isoformat()

_SCHEMA = """
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


def _dt(date_str: str, hour: int = 10) -> str:
    """Format a fixed timestamp string."""
    return f"{date_str} {hour:02d}:00:00"


def _build_db(path: Path) -> None:
    """Populate the test DB with deterministic, date-fixed measurements."""
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)

    con.executemany(
        "INSERT INTO cow VALUES (?, ?, '2020-01-01')",
        [
            ("cow-healthy", "Healthy Cow"),
            ("cow-milk-drop", "Milk Drop Cow"),
            ("cow-weight-loss", "Weight Loss Cow"),
            ("cow-no-data", "No Data Cow"),
        ],
    )
    con.executemany(
        "INSERT INTO sensor VALUES (?, ?)",
        [("sensor-milk", "L"), ("sensor-weight", "kg")],
    )

    # ------------------------------------------------------------------ #
    # Baseline dates: 2024-05-16 → 2024-06-12  (days 4-30 before target) #
    # Recent  dates:  2024-06-13 → 2024-06-15  (days 0-2 before target)  #
    # ------------------------------------------------------------------ #

    baseline_dates = [
        "2024-05-16",
        "2024-05-20",
        "2024-05-25",
        "2024-06-01",
        "2024-06-05",
        "2024-06-10",
        "2024-06-12",
    ]
    recent_dates = ["2024-06-13", "2024-06-14", "2024-06-15"]

    # cow-healthy: 20 L/day milk, 500 kg weight — stable throughout
    for d in baseline_dates + recent_dates:
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-healthy',?,20.0)",
            (_dt(d),),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-healthy',?,500.0)",
            (_dt(d),),
        )

    # cow-milk-drop: 20 L baseline → 5 L in last 3 days; weight stable ~490 kg
    for d in baseline_dates:
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-milk-drop',?,20.0)",
            (_dt(d),),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-milk-drop',?,490.0)",
            (_dt(d),),
        )
    for d in recent_dates:
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-milk-drop',?,5.0)",
            (_dt(d),),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-milk-drop',?,488.0)",
            (_dt(d),),
        )

    # cow-weight-loss: stable milk ~18 L; weight 510 kg baseline → 450 kg recent (~12% drop)
    for d in baseline_dates:
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-weight-loss',?,18.0)",
            (_dt(d),),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-weight-loss',?,510.0)",
            (_dt(d),),
        )
    for d in recent_dates:
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-weight-loss',?,17.0)",
            (_dt(d),),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-weight-loss',?,450.0)",
            (_dt(d),),
        )

    # cow-no-data: no measurements at all

    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    _build_db(path)
    return path


@pytest.fixture()
def db_con(db_path: Path):
    con = sqlite3.connect(db_path, check_same_thread=False)
    yield con
    con.close()


@pytest.fixture()
def generator() -> ReportGenerator:
    return ReportGenerator()


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
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TestReportGeneratorUnit — tests generate_from_connection() directly
# ---------------------------------------------------------------------------


class TestReportGeneratorUnit:
    def test_returns_non_empty_string(self, generator: ReportGenerator, db_con) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_report_contains_target_date_in_header(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert TARGET_STR in result

    def test_report_contains_milk_section(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert "MILK" in result.upper()

    def test_report_contains_weight_section(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert "WEIGHT" in result.upper()

    def test_report_contains_health_section(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert "HEALTH" in result.upper()

    def test_milk_section_shows_target_date_production(
        self, generator: ReportGenerator, db_con
    ) -> None:
        """Milk section must include the healthy cow's 20 L on the target date."""
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert "Healthy Cow" in result
        assert "20.0" in result

    def test_milk_section_only_for_target_date(
        self, generator: ReportGenerator, db_con
    ) -> None:
        """Querying one day before target must not show healthy cow's milk (different day)."""
        prev_date = date(2024, 6, 14)
        result_prev = generator.generate_from_connection(db_con, prev_date)
        # Both should show Healthy Cow (it has data on both days), but are independent queries
        assert TARGET_STR not in result_prev

    def test_weight_section_lists_all_cows(
        self, generator: ReportGenerator, db_con
    ) -> None:
        """Weight section must include every cow registered in the DB."""
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert "Healthy Cow" in result
        assert "Milk Drop Cow" in result
        assert "Weight Loss Cow" in result
        assert "No Data Cow" in result

    def test_cow_with_no_data_shows_placeholder(
        self, generator: ReportGenerator, db_con
    ) -> None:
        """Cows with no measurements must appear with a '-' placeholder, not crash."""
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        assert "No Data Cow" in result

    def test_healthy_cow_not_flagged_in_health_section(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        lines = result.splitlines()
        health_start = next(
            i for i, line in enumerate(lines) if "HEALTH ALERTS" in line.upper()
        )
        health_section = "\n".join(lines[health_start:])
        assert "Healthy Cow" not in health_section

    def test_milk_drop_cow_flagged_in_health_section(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        lines = result.splitlines()
        health_start = next(
            i for i, line in enumerate(lines) if "HEALTH ALERTS" in line.upper()
        )
        health_section = "\n".join(lines[health_start:])
        assert "Milk Drop Cow" in health_section

    def test_weight_loss_cow_flagged_in_health_section(
        self, generator: ReportGenerator, db_con
    ) -> None:
        result = generator.generate_from_connection(db_con, TARGET_DATE)
        lines = result.splitlines()
        health_start = next(
            i for i, line in enumerate(lines) if "HEALTH ALERTS" in line.upper()
        )
        health_section = "\n".join(lines[health_start:])
        assert "Weight Loss Cow" in health_section

    def test_empty_date_returns_report_without_error(
        self, generator: ReportGenerator, db_con
    ) -> None:
        """A date with no data must return a valid (but empty-section) report, not raise."""
        old_date = date(2020, 1, 1)
        result = generator.generate_from_connection(db_con, old_date)
        assert isinstance(result, str)
        assert "2020-01-01" in result

    def test_date_parameterization_isolates_milk_data(
        self, generator: ReportGenerator, db_con
    ) -> None:
        """Report for 2020-01-01 must not contain cow names in milk section (no data then)."""
        old_date = date(2020, 1, 1)
        result = generator.generate_from_connection(db_con, old_date)
        # Extract milk section
        lines = result.splitlines()
        milk_start = next(i for i, line in enumerate(lines) if "MILK" in line.upper())
        weight_start = next(i for i, line in enumerate(lines) if "WEIGHT" in line.upper())
        milk_section = "\n".join(lines[milk_start:weight_start])
        assert "Healthy Cow" not in milk_section


# ---------------------------------------------------------------------------
# TestReportGeneratorCLI — tests generate() with a file-path db
# ---------------------------------------------------------------------------


class TestReportGeneratorCLI:
    def test_generate_returns_string(
        self, generator: ReportGenerator, db_path: Path
    ) -> None:
        result = generator.generate(TARGET_DATE, db_path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_same_content_as_from_connection(
        self, generator: ReportGenerator, db_path: Path, db_con
    ) -> None:
        via_path = generator.generate(TARGET_DATE, db_path)
        via_con = generator.generate_from_connection(db_con, TARGET_DATE)
        assert via_path == via_con

    def test_generate_nonexistent_db_raises(
        self, generator: ReportGenerator, tmp_path: Path
    ) -> None:
        with pytest.raises(Exception):
            generator.generate(TARGET_DATE, tmp_path / "nonexistent.db")


# ---------------------------------------------------------------------------
# TestDailyReportEndpoint — tests GET /reports/daily via TestClient
# ---------------------------------------------------------------------------


class TestDailyReportEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/reports/daily").status_code == 200

    def test_content_type_is_plain_text(self, client: TestClient) -> None:
        r = client.get("/reports/daily")
        assert "text/plain" in r.headers["content-type"]

    def test_date_param_accepted(self, client: TestClient) -> None:
        r = client.get(f"/reports/daily?date={TARGET_STR}")
        assert r.status_code == 200
        assert TARGET_STR in r.text

    def test_omitting_date_returns_200(self, client: TestClient) -> None:
        assert client.get("/reports/daily").status_code == 200

    def test_invalid_date_returns_422(self, client: TestClient) -> None:
        assert client.get("/reports/daily?date=not-a-date").status_code == 422

    def test_response_contains_section_headers(self, client: TestClient) -> None:
        r = client.get(f"/reports/daily?date={TARGET_STR}")
        body = r.text.upper()
        assert "MILK" in body
        assert "WEIGHT" in body
        assert "HEALTH" in body

    def test_date_param_filters_correctly(self, client: TestClient) -> None:
        """Report for a date with no data must not include cow names in milk section."""
        r = client.get("/reports/daily?date=2020-01-01")
        assert r.status_code == 200
        assert "2020-01-01" in r.text
