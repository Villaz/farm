"""Tests for MeasurementLoader in loader.py."""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app, get_db
from init_db import init_db
from loader import MeasurementLoader, MeasurementValidationReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ts(dt: str) -> float:
    """Convierte una fecha ISO a Unix timestamp float."""
    return pd.Timestamp(dt).timestamp()


@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """Dataset limpio sin anomalías."""
    return pd.DataFrame(
        {
            "sensor_id": ["s-1", "s-2", "s-3"],
            "cow_id": ["c-1", "c-2", "c-3"],
            "timestamp": [
                _make_ts("2022-01-01"),
                _make_ts("2022-06-15"),
                _make_ts("2023-03-20"),
            ],
            "value": [4.72, 549.51, 10.0],
        }
    )


@pytest.fixture()
def parquet_file(sample_df: pd.DataFrame, tmp_path: Path) -> Path:
    """Escribe sample_df en un parquet temporal y devuelve la ruta."""
    path = tmp_path / "measurements.parquet"
    sample_df.to_parquet(path, index=False)
    return path


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Initialised SQLite database path."""
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture()
def http_client(db_path: Path):
    """FastAPI TestClient wired to the test database."""

    def override_get_db():
        con = sqlite3.connect(db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
        finally:
            con.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def loader(parquet_file: Path, http_client) -> MeasurementLoader:
    return MeasurementLoader(parquet_path=parquet_file, http_client=http_client)


@pytest.fixture()
def populated_db(db_path: Path) -> Path:
    """Inserts prerequisite sensor and cow rows so FK constraints are satisfied."""
    with sqlite3.connect(db_path) as con:
        for i in range(1, 4):
            con.execute(f"INSERT INTO sensor VALUES ('s-{i}', 'L')")
            con.execute(f"INSERT INTO cow VALUES ('c-{i}', 'Cow {i}', '2020-01-01')")
    return db_path


# ---------------------------------------------------------------------------
# MeasurementLoader.load
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_dataframe(self, loader: MeasurementLoader) -> None:
        """load() debe devolver un DataFrame."""
        assert isinstance(loader.load(), pd.DataFrame)

    def test_expected_columns(self, loader: MeasurementLoader) -> None:
        """El DataFrame debe tener las columnas sensor_id, cow_id, timestamp y value."""
        df = loader.load()
        assert set(df.columns) == {"sensor_id", "cow_id", "timestamp", "value"}

    def test_row_count(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """El número de filas debe coincidir con el parquet original."""
        assert len(loader.load()) == len(sample_df)

    def test_timestamp_parsed_as_datetime(self, loader: MeasurementLoader) -> None:
        """timestamp se debe convertir de Unix epoch a datetime."""
        df = loader.load()
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])

    def test_timestamp_values_correct(self, loader: MeasurementLoader) -> None:
        """Los valores de timestamp deben corresponderse con las fechas originales."""
        df = loader.load()
        assert df["timestamp"].iloc[0].year == 2022
        assert df["timestamp"].iloc[0].month == 1

    def test_file_not_found_raises(self, http_client) -> None:
        """load() debe lanzar FileNotFoundError si el parquet no existe."""
        loader = MeasurementLoader(
            parquet_path=Path("nope.parquet"), http_client=http_client
        )
        with pytest.raises(FileNotFoundError):
            loader.load()


# ---------------------------------------------------------------------------
# MeasurementLoader.validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_clean_data_no_anomalies(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """Un dataset limpio no debe reportar anomalías."""
        df = loader.load()
        report = loader.validate(df)
        assert report.null_values.empty
        assert report.negative_values.empty
        assert report.future_timestamps.empty

    def test_returns_validation_report_type(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe devolver MeasurementValidationReport."""
        df = loader.load()
        assert isinstance(loader.validate(df), MeasurementValidationReport)

    def test_detects_null_values(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar filas con value nulo."""
        df = sample_df.copy()
        df.loc[0, "value"] = None
        report = loader.validate(df)
        assert len(report.null_values) == 1

    def test_detects_negative_values(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar filas con value negativo."""
        df = sample_df.copy()
        df.loc[1, "value"] = -1.0
        report = loader.validate(df)
        assert not report.negative_values.empty
        assert (report.negative_values["value"] < 0).all()

    def test_detects_future_timestamps(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar timestamps futuros."""
        df = loader.load()
        future_ts = pd.Timestamp.now() + timedelta(days=30)
        extra = pd.DataFrame(
            {
                "sensor_id": ["s-99"],
                "cow_id": ["c-99"],
                "timestamp": [future_ts],
                "value": [5.0],
            }
        )
        df_with_future = pd.concat([df, extra], ignore_index=True)
        report = loader.validate(df_with_future)
        assert not report.future_timestamps.empty
        assert "s-99" in report.future_timestamps["sensor_id"].values

    def test_null_count_matches(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """El número de filas en null_values debe coincidir con el conteo real de nulos."""
        df = sample_df.copy()
        df.loc[0, "value"] = None
        df.loc[2, "value"] = None
        report = loader.validate(df)
        assert len(report.null_values) == 2

    def test_negative_values_all_negative(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """Todas las filas en negative_values deben tener value < 0."""
        df = sample_df.copy()
        df.loc[0, "value"] = -1.0
        df.loc[2, "value"] = -0.5
        report = loader.validate(df)
        assert len(report.negative_values) == 2
        assert (report.negative_values["value"] < 0).all()


# ---------------------------------------------------------------------------
# MeasurementLoader.validate - Row filtering (filas inválidas se eliminan)
# ---------------------------------------------------------------------------


class TestValidateFiltering:
    def test_removes_null_values(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con valores nulos."""
        df = sample_df.copy()
        df.loc[0, "value"] = None
        df.loc[1, "sensor_id"] = None
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 2
        assert not report.null_values.empty

    def test_removes_negative_values(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con valores negativos."""
        df = sample_df.copy()
        df.loc[0, "value"] = -1.0
        df.loc[1, "value"] = -0.5
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 2
        assert not report.negative_values.empty

    def test_removes_future_timestamps(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con timestamps futuros."""
        df = loader.load()
        future_ts = (pd.Timestamp.now() + timedelta(days=30)).as_unit("s")
        df.loc[0, "timestamp"] = future_ts
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 1
        assert not report.future_timestamps.empty

    def test_removes_duplicate_pk(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con PK compuesta duplicada, manteniendo la primera."""
        df = sample_df.copy()
        duplicate_row = df.iloc[0:1].copy()
        df = pd.concat([df, duplicate_row], ignore_index=True)
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 1
        pk_mask = (
            (df["sensor_id"] == sample_df.iloc[0]["sensor_id"])
            & (df["cow_id"] == sample_df.iloc[0]["cow_id"])
            & (df["timestamp"] == sample_df.iloc[0]["timestamp"])
        )
        assert pk_mask.sum() == 1

    def test_dataframe_modified_inplace(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe modificar el dataframe original (in-place)."""
        df = sample_df.copy()
        df.loc[0, "value"] = -1.0
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 1

    def test_valid_rows_remain(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe mantener filas válidas después de filtrar."""
        df = sample_df.copy()
        _ = loader.validate(df)
        assert len(df) == len(sample_df)


# ---------------------------------------------------------------------------
# MeasurementLoader.save
# ---------------------------------------------------------------------------


class TestSave:
    def test_row_count_matches(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame, populated_db: Path
    ) -> None:
        """La tabla measurement debe contener el mismo número de filas."""
        df = loader.load()
        loader.save(df)
        with sqlite3.connect(populated_db) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == len(sample_df)

    def test_columns_stored_correctly(
        self, loader: MeasurementLoader, populated_db: Path
    ) -> None:
        """Las columnas almacenadas deben ser sensor_id, cow_id, timestamp y value."""
        df = loader.load()
        loader.save(df)
        with sqlite3.connect(populated_db) as con:
            cols = [
                d[0]
                for d in con.execute("SELECT * FROM measurement LIMIT 1").description
            ]
        assert "sensor_id" in cols
        assert "cow_id" in cols
        assert "timestamp" in cols
        assert "value" in cols

    def test_idempotent_on_duplicate(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame, populated_db: Path
    ) -> None:
        """Llamar save() dos veces no debe duplicar filas (409 silenciado)."""
        df = loader.load()
        loader.save(df)
        loader.save(df)
        with sqlite3.connect(populated_db) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == len(sample_df)

    def test_returns_row_count(
        self, loader: MeasurementLoader, populated_db: Path
    ) -> None:
        """save() debe devolver el número de filas del DataFrame."""
        df = loader.load()
        assert loader.save(df) == len(df)

    def test_skips_null_values(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame, populated_db: Path
    ) -> None:
        """save() debe omitir las filas con value nulo."""
        df = loader.load()
        df.loc[0, "value"] = float("nan")
        loader.save(df)
        with sqlite3.connect(populated_db) as con:
            count = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
        assert count == len(sample_df) - 1

    def test_composite_pk_enforced(
        self, loader: MeasurementLoader, populated_db: Path
    ) -> None:
        """Insertar una tripleta (sensor_id, cow_id, timestamp) duplicada debe lanzar IntegrityError."""
        df = loader.load()
        loader.save(df)
        with sqlite3.connect(populated_db) as con:
            row = con.execute(
                "SELECT sensor_id, cow_id, timestamp FROM measurement LIMIT 1"
            ).fetchone()
            assert row is not None, (
                "No se insertaron filas: save() no persistió ningún dato"
            )
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO measurement (sensor_id, cow_id, timestamp, value) VALUES (?, ?, ?, ?)",
                    (*row, 0.0),
                )

    def test_table_has_fk_to_sensor_and_cow(self, db_path: Path) -> None:
        """El DDL de measurement debe declarar FK a sensor y cow."""
        with sqlite3.connect(db_path) as con:
            ddl = (
                con.execute("SELECT sql FROM sqlite_master WHERE name='measurement'")
                .fetchone()[0]
                .upper()
            )
        assert "FOREIGN KEY" in ddl
        assert "SENSOR" in ddl
        assert "COW" in ddl

    def test_save_uses_batch_endpoint(
        self,
        loader: MeasurementLoader,
        populated_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """save() debe llamar al endpoint /load/measurements (batch) en vez de /measurements por fila."""
        called_urls: list[str] = []
        original_post = loader._client.post

        def spy_post(url: str, **kwargs):
            called_urls.append(url)
            return original_post(url, **kwargs)

        monkeypatch.setattr(loader._client, "post", spy_post)

        df = loader.load()
        loader.save(df)

        assert any(url == "/load/measurements" for url in called_urls), (
            f"Se esperaba una llamada a /load/measurements, pero se llamó a: {called_urls}"
        )
        assert not any(url == "/measurements" for url in called_urls), (
            "save() no debe llamar a /measurements por fila"
        )


# ---------------------------------------------------------------------------
# MeasurementLoader.run (pipeline completo)
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_returns_rows_and_report(
        self, loader: MeasurementLoader, populated_db: Path
    ) -> None:
        """run() debe devolver (int, MeasurementValidationReport)."""
        rows, report = loader.run()
        assert isinstance(rows, int)
        assert isinstance(report, MeasurementValidationReport)

    def test_run_row_count(
        self, loader: MeasurementLoader, sample_df: pd.DataFrame, populated_db: Path
    ) -> None:
        """run() debe procesar el mismo número de filas que hay en el parquet."""
        rows, _ = loader.run()
        assert rows == len(sample_df)

    def test_run_with_real_parquet(self, tmp_path: Path) -> None:
        """run() debe procesar el parquet real del proyecto sin errores."""
        from loader import CowLoader, SensorLoader

        cows = Path("data/cows.parquet")
        sensors = Path("data/sensors.parquet")
        real = Path("data/measurements.parquet")
        if not all(p.exists() for p in (cows, sensors, real)):
            pytest.skip("Ficheros parquet no disponibles")

        db = tmp_path / "real.db"
        init_db(db)

        def override_get_db():
            con = sqlite3.connect(db, check_same_thread=False)
            con.execute("PRAGMA foreign_keys = ON")
            try:
                yield con
            finally:
                con.close()

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                CowLoader(parquet_path=cows, http_client=client).run()
                SensorLoader(parquet_path=sensors, http_client=client).run()
                loader = MeasurementLoader(parquet_path=real, http_client=client)
                rows, report = loader.run()
        finally:
            app.dependency_overrides.clear()

        assert rows == 556809  # 563627 original - 3393 nulos - 3425 negativos
        assert len(report.null_values) == 3393
        assert len(report.negative_values) == 3425
        assert report.future_timestamps.empty
