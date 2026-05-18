"""Tests for GET /insights/* endpoints.

Three endpoints:
- GET /insights/milk-production  — milk production per cow per day (last 30 days)
- GET /insights/weights          — current weight + 30-day avg per cow
- GET /insights/health           — cows that might be ill (bonus)
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_schema


def _ts(days_ago: float, hour: int = 10) -> str:
    """Devuelve un timestamp ISO relativo a ahora (UTC)."""
    dt = datetime.utcnow() - timedelta(days=days_ago)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Base de datos de test con cuatro vacas y datos de 30 días.

    - cow-healthy:     producción y peso estables (no enfermedad).
    - cow-milk-drop:   producción normal días 4-30, cae >30% en últimos 3 días.
    - cow-weight-loss: peso normal días 4-30, cae ~12% en últimos 3 días.
    - cow-no-data:     sin mediciones.
    """
    path = tmp_path / "test.db"
    create_schema(path)
    con = sqlite3.connect(path)

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

    # healthy: 20 L/day, 500 kg — 30 días continuos
    for d in range(30):
        ts = _ts(29 - d)
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-healthy',?,20.0)", (ts,)
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-healthy',?,500.0)",
            (ts,),
        )

    # milk-drop: 20 L/day días 4-30, cae a 5 L/day en últimos 3 días
    for d in range(27):
        ts = _ts(29 - d)
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-milk-drop',?,20.0)",
            (ts,),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-milk-drop',?,490.0)",
            (ts,),
        )
    for d in range(3):
        ts = _ts(2 - d)
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-milk-drop',?,5.0)",
            (ts,),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-milk-drop',?,488.0)",
            (ts,),
        )

    # weight-loss: leche estable, peso cae ~12% en últimos 3 días
    for d in range(27):
        ts = _ts(29 - d)
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-weight-loss',?,18.0)",
            (ts,),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-weight-loss',?,510.0)",
            (ts,),
        )
    for d in range(3):
        ts = _ts(2 - d)
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-milk','cow-weight-loss',?,17.0)",
            (ts,),
        )
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-weight-loss',?,450.0)",
            (ts,),
        )

    con.commit()
    con.close()
    return path


# ---------------------------------------------------------------------------
# GET /insights/milk-production
# ---------------------------------------------------------------------------


class TestMilkProduction:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/insights/milk-production").status_code == 200

    def test_returns_list(self, client: TestClient) -> None:
        body = client.get("/insights/milk-production").json()
        assert isinstance(body, list)

    def test_entry_has_required_fields(self, client: TestClient) -> None:
        body = client.get("/insights/milk-production").json()
        entry = next(e for e in body if e["cow_id"] == "cow-healthy")
        assert "cow_id" in entry
        assert "cow_name" in entry
        assert "date" in entry
        assert "total_liters" in entry

    def test_cow_name_is_included(self, client: TestClient) -> None:
        body = client.get("/insights/milk-production").json()
        entry = next(e for e in body if e["cow_id"] == "cow-healthy")
        assert entry["cow_name"] == "Healthy Cow"

    def test_total_liters_aggregated_per_day(self, client: TestClient) -> None:
        """Cada día de la healthy cow debe sumar exactamente 20 L."""
        body = client.get("/insights/milk-production").json()
        healthy = [e for e in body if e["cow_id"] == "cow-healthy"]
        assert all(e["total_liters"] == pytest.approx(20.0) for e in healthy)

    def test_only_liter_sensors_included(self, client: TestClient) -> None:
        """Las mediciones en kg no deben aparecer en producción de leche."""
        body = client.get("/insights/milk-production").json()
        # Si se incluyesen kg, los totales superarían 20 (500 kg > 20 L)
        healthy = [e for e in body if e["cow_id"] == "cow-healthy"]
        assert all(e["total_liters"] <= 25.0 for e in healthy)

    def test_cow_without_milk_data_not_in_response(self, client: TestClient) -> None:
        body = client.get("/insights/milk-production").json()
        assert all(e["cow_id"] != "cow-no-data" for e in body)

    def test_covers_last_30_days(self, client: TestClient) -> None:
        body = client.get("/insights/milk-production").json()
        healthy = [e for e in body if e["cow_id"] == "cow-healthy"]
        assert len(healthy) <= 30

    def test_separate_entry_per_day(self, client: TestClient) -> None:
        """Cada día debe ser una entrada independiente, no una suma global."""
        body = client.get("/insights/milk-production").json()
        healthy = [e for e in body if e["cow_id"] == "cow-healthy"]
        dates = [e["date"] for e in healthy]
        assert len(dates) == len(set(dates))  # sin duplicados de fecha


# ---------------------------------------------------------------------------
# GET /insights/weights
# ---------------------------------------------------------------------------


class TestWeights:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/insights/weights").status_code == 200

    def test_returns_all_cows(self, client: TestClient) -> None:
        body = client.get("/insights/weights").json()
        assert isinstance(body, list)
        assert len(body) == 4

    def test_entry_has_required_fields(self, client: TestClient) -> None:
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-healthy")
        assert "cow_id" in entry
        assert "cow_name" in entry
        assert "current_weight_kg" in entry
        assert "avg_weight_30d_kg" in entry

    def test_cow_name_is_included(self, client: TestClient) -> None:
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-healthy")
        assert entry["cow_name"] == "Healthy Cow"

    def test_current_weight_is_most_recent(self, client: TestClient) -> None:
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-healthy")
        assert entry["current_weight_kg"] == pytest.approx(500.0)

    def test_avg_weight_30d_correct(self, client: TestClient) -> None:
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-healthy")
        assert entry["avg_weight_30d_kg"] == pytest.approx(500.0)

    def test_cow_with_no_weight_data_has_null_values(self, client: TestClient) -> None:
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-no-data")
        assert entry["current_weight_kg"] is None
        assert entry["avg_weight_30d_kg"] is None

    def test_weight_loss_cow_avg_differs_from_current(self, client: TestClient) -> None:
        """La vaca con pérdida de peso debe tener current < avg_30d."""
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-weight-loss")
        assert entry["current_weight_kg"] < entry["avg_weight_30d_kg"]

    def test_results_ordered_by_cow_name(self, client: TestClient) -> None:
        """La lista debe estar ordenada alfabéticamente por nombre de vaca."""
        body = client.get("/insights/weights").json()
        names = [e["cow_name"] for e in body]
        assert names == sorted(names)

    def test_weight_loss_cow_current_weight_is_latest_value(
        self, client: TestClient
    ) -> None:
        """current_weight_kg debe ser el valor más reciente, no el más antiguo."""
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-weight-loss")
        # La fixture inserta 510 kg durante días 4-30 y 450 kg en los últimos 3 días.
        assert entry["current_weight_kg"] == pytest.approx(450.0)


@pytest.fixture()
def db_path_old_data(tmp_path: Path) -> Path:
    """DB con una vaca que tiene mediciones dentro y fuera de la ventana de 30 días.

    - cow-old: peso 600 kg hace 35 días (fuera de ventana), 400 kg en días 0-4 (dentro).
      → current_weight_kg debe ser 400, avg_weight_30d_kg debe ser 400 (no 500).
    """
    path = tmp_path / "test_old.db"
    create_schema(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO cow VALUES ('cow-old', 'Old Data Cow', '2018-01-01')")
    con.execute("INSERT INTO sensor VALUES ('sensor-weight', 'kg')")
    # Medición fuera de la ventana de 30 días
    ts_old = (datetime.utcnow() - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "INSERT INTO measurement VALUES ('sensor-weight','cow-old',?,600.0)", (ts_old,)
    )
    # Mediciones dentro de la ventana
    for d in range(5):
        ts = (datetime.utcnow() - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            "INSERT INTO measurement VALUES ('sensor-weight','cow-old',?,400.0)", (ts,)
        )
    con.commit()
    con.close()
    return path


class TestWeightsOldData:
    """Verifica que la ventana de 30 días excluye mediciones antiguas."""

    @pytest.fixture()
    def client(self, db_path_old_data: Path) -> TestClient:
        from api.main import app, get_db

        def override():
            con = sqlite3.connect(db_path_old_data, check_same_thread=False)
            con.execute("PRAGMA foreign_keys = ON")
            try:
                yield con
            finally:
                con.close()

        app.dependency_overrides[get_db] = override
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_avg_excludes_measurements_older_than_30_days(
        self, client: TestClient
    ) -> None:
        """La medición de día 35 (600 kg) no debe afectar al promedio de 30 días."""
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-old")
        assert entry["avg_weight_30d_kg"] == pytest.approx(400.0)

    def test_current_weight_is_400_not_600(self, client: TestClient) -> None:
        """current_weight_kg debe ser el valor más reciente (400), no el antiguo (600)."""
        body = client.get("/insights/weights").json()
        entry = next(e for e in body if e["cow_id"] == "cow-old")
        assert entry["current_weight_kg"] == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# GET /insights/health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/insights/health").status_code == 200

    def test_returns_list(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        assert isinstance(body, list)

    def test_entry_has_required_fields(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        assert len(body) > 0, (
            "Se esperan vacas potencialmente enfermas en los datos de test"
        )
        entry = body[0]
        assert "cow_id" in entry
        assert "cow_name" in entry
        assert "reasons" in entry

    def test_healthy_cow_not_flagged(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        flagged = {e["cow_id"] for e in body}
        assert "cow-healthy" not in flagged

    def test_milk_drop_cow_is_flagged(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        flagged = {e["cow_id"] for e in body}
        assert "cow-milk-drop" in flagged

    def test_milk_drop_cow_has_milk_reason(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        entry = next(e for e in body if e["cow_id"] == "cow-milk-drop")
        assert any(
            "milk" in r.lower() or "leche" in r.lower() for r in entry["reasons"]
        )

    def test_weight_loss_cow_is_flagged(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        flagged = {e["cow_id"] for e in body}
        assert "cow-weight-loss" in flagged

    def test_weight_loss_cow_has_weight_reason(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        entry = next(e for e in body if e["cow_id"] == "cow-weight-loss")
        assert any(
            "weight" in r.lower() or "peso" in r.lower() for r in entry["reasons"]
        )

    def test_reasons_is_a_non_empty_list(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        for entry in body:
            assert isinstance(entry["reasons"], list)
            assert len(entry["reasons"]) >= 1

    def test_cow_with_no_data_not_in_response(self, client: TestClient) -> None:
        """cow-no-data no tiene mediciones; no debe aparecer en el resultado."""
        body = client.get("/insights/health").json()
        flagged = {e["cow_id"] for e in body}
        assert "cow-no-data" not in flagged

    def test_results_ordered_by_cow_name(self, client: TestClient) -> None:
        """El resultado debe estar ordenado alfabéticamente por nombre de vaca."""
        body = client.get("/insights/health").json()
        names = [e["cow_name"] for e in body]
        assert names == sorted(names)

    def test_cow_name_is_included_in_response(self, client: TestClient) -> None:
        """cow_name debe ser el nombre real, no el id."""
        body = client.get("/insights/health").json()
        entry = next(e for e in body if e["cow_id"] == "cow-milk-drop")
        assert entry["cow_name"] == "Milk Drop Cow"


@pytest.fixture()
def db_path_health_edge(tmp_path: Path) -> Path:
    """DB con vacas que tienen solo un tipo de medición (peso O leche, no ambas).

    - cow-weight-only: pierde >5% peso en últimos 3 días, sin mediciones de leche.
    - cow-milk-only:   cae >30% leche en últimos 3 días, sin mediciones de peso.
    - cow-both-issues: cae leche Y pierde peso simultáneamente.
    """
    path = tmp_path / "health_edge.db"
    create_schema(path)
    con = sqlite3.connect(path)

    for cid, name in [
        ("cow-weight-only", "Weight Only Cow"),
        ("cow-milk-only", "Milk Only Cow"),
        ("cow-both-issues", "Both Issues Cow"),
    ]:
        con.execute(f"INSERT INTO cow VALUES ('{cid}', '{name}', '2020-01-01')")

    con.execute("INSERT INTO sensor VALUES ('s-milk', 'L')")
    con.execute("INSERT INTO sensor VALUES ('s-weight', 'kg')")

    # cow-weight-only: 500 kg baseline (días 4-30), 400 kg reciente (días 0-2)
    for d in range(4, 30):
        ts = _ts(d)
        con.execute("INSERT INTO measurement VALUES ('s-weight','cow-weight-only',?,500.0)", (ts,))
    for d in range(3):
        ts = _ts(d)
        con.execute("INSERT INTO measurement VALUES ('s-weight','cow-weight-only',?,400.0)", (ts,))

    # cow-milk-only: 20 L/day baseline (días 4-30), 5 L/day reciente (días 0-2)
    for d in range(4, 30):
        ts = _ts(d)
        con.execute("INSERT INTO measurement VALUES ('s-milk','cow-milk-only',?,20.0)", (ts,))
    for d in range(3):
        ts = _ts(d)
        con.execute("INSERT INTO measurement VALUES ('s-milk','cow-milk-only',?,5.0)", (ts,))

    # cow-both-issues: ambas caídas
    for d in range(4, 30):
        ts = _ts(d)
        con.execute("INSERT INTO measurement VALUES ('s-milk','cow-both-issues',?,20.0)", (ts,))
        con.execute("INSERT INTO measurement VALUES ('s-weight','cow-both-issues',?,500.0)", (ts,))
    for d in range(3):
        ts = _ts(d)
        con.execute("INSERT INTO measurement VALUES ('s-milk','cow-both-issues',?,5.0)", (ts,))
        con.execute("INSERT INTO measurement VALUES ('s-weight','cow-both-issues',?,400.0)", (ts,))

    con.commit()
    con.close()
    return path


class TestHealthEdgeCases:
    """Casos de borde: vaca con solo peso, solo leche, o ambas alertas."""

    @pytest.fixture()
    def client(self, db_path_health_edge: Path) -> TestClient:
        from api.main import app, get_db

        def override():
            con = sqlite3.connect(db_path_health_edge, check_same_thread=False)
            con.execute("PRAGMA foreign_keys = ON")
            try:
                yield con
            finally:
                con.close()

        app.dependency_overrides[get_db] = override
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_weight_only_cow_is_flagged(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        flagged = {e["cow_id"] for e in body}
        assert "cow-weight-only" in flagged

    def test_weight_only_cow_has_no_milk_reason(self, client: TestClient) -> None:
        """Sin mediciones de leche no debe aparecer razón de milk drop."""
        body = client.get("/insights/health").json()
        entry = next(e for e in body if e["cow_id"] == "cow-weight-only")
        assert not any("milk" in r.lower() for r in entry["reasons"])

    def test_milk_only_cow_is_flagged(self, client: TestClient) -> None:
        body = client.get("/insights/health").json()
        flagged = {e["cow_id"] for e in body}
        assert "cow-milk-only" in flagged

    def test_milk_only_cow_has_no_weight_reason(self, client: TestClient) -> None:
        """Sin mediciones de peso no debe aparecer razón de weight loss."""
        body = client.get("/insights/health").json()
        entry = next(e for e in body if e["cow_id"] == "cow-milk-only")
        assert not any("weight" in r.lower() for r in entry["reasons"])

    def test_both_issues_cow_has_two_reasons(self, client: TestClient) -> None:
        """Una vaca con caída de leche Y peso debe tener dos razones."""
        body = client.get("/insights/health").json()
        entry = next(e for e in body if e["cow_id"] == "cow-both-issues")
        assert len(entry["reasons"]) == 2
