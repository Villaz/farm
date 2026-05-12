"""Tests for SensorLoader in loader.py."""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app, get_db
from init_db import init_db
from loader import SensorLoader, SensorValidationReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """Dataset limpio sin anomalías."""
    return pd.DataFrame(
        {
            "id": ["uuid-1", "uuid-2", "uuid-3"],
            "unit": ["L", "kg", "L"],
        }
    )


@pytest.fixture()
def parquet_file(sample_df: pd.DataFrame, tmp_path: Path) -> Path:
    """Escribe sample_df en un parquet temporal y devuelve la ruta."""
    path = tmp_path / "sensors.parquet"
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
def loader(parquet_file: Path, http_client) -> SensorLoader:
    return SensorLoader(parquet_path=parquet_file, http_client=http_client)


# ---------------------------------------------------------------------------
# SensorLoader.load
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_dataframe(self, loader: SensorLoader) -> None:
        """load() debe devolver un DataFrame."""
        assert isinstance(loader.load(), pd.DataFrame)

    def test_expected_columns(self, loader: SensorLoader) -> None:
        """El DataFrame debe tener las columnas id y unit."""
        df = loader.load()
        assert set(df.columns) == {"id", "unit"}

    def test_row_count(self, loader: SensorLoader, sample_df: pd.DataFrame) -> None:
        """El número de filas debe coincidir con el parquet original."""
        assert len(loader.load()) == len(sample_df)

    def test_column_types_are_string(self, loader: SensorLoader) -> None:
        """Las columnas id y unit deben ser de tipo object/string."""
        df = loader.load()
        assert df["id"].dtype == object or pd.api.types.is_string_dtype(df["id"])
        assert df["unit"].dtype == object or pd.api.types.is_string_dtype(df["unit"])

    def test_file_not_found_raises(self, http_client) -> None:
        """load() debe lanzar FileNotFoundError si el parquet no existe."""
        loader = SensorLoader(
            parquet_path=Path("nope.parquet"), http_client=http_client
        )
        with pytest.raises(FileNotFoundError):
            loader.load()


# ---------------------------------------------------------------------------
# SensorLoader.validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_clean_data_no_anomalies(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """Un dataset limpio no debe reportar anomalías."""
        report = loader.validate(sample_df)
        assert report.null_values.empty
        assert report.duplicate_ids.empty
        assert report.unknown_units.empty

    def test_returns_validation_report_type(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe devolver SensorValidationReport."""
        assert isinstance(loader.validate(sample_df), SensorValidationReport)

    def test_detects_null_id(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar filas con id nulo."""
        df = sample_df.copy()
        df.loc[0, "id"] = None
        report = loader.validate(df)
        assert not report.null_values.empty
        assert len(report.null_values) == 1

    def test_detects_null_unit(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar filas con unit nula."""
        df = sample_df.copy()
        df.loc[1, "unit"] = None
        report = loader.validate(df)
        assert not report.null_values.empty

    def test_detects_duplicate_ids(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar IDs duplicados."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-1"], "unit": ["kg"]}),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert not report.duplicate_ids.empty
        assert set(report.duplicate_ids["id"]) == {"uuid-1"}

    def test_duplicate_id_count(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """duplicate_ids debe contener una sola fila por id duplicado (sin repetir el valor)."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-2", "uuid-2"], "unit": ["L", "kg"]}),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert len(report.duplicate_ids) == 1

    def test_detects_unknown_units(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar unidades fuera del conjunto esperado {'L', 'kg'}."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-99"], "unit": ["ml"]}),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert not report.unknown_units.empty
        assert set(report.unknown_units["unit"]) == {"ml"}

    def test_known_units_not_flagged(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """Las unidades 'L' y 'kg' no deben aparecer en unknown_units."""
        report = loader.validate(sample_df)
        assert report.unknown_units.empty

    def test_multiple_unknown_units(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """Debe detectar múltiples unidades desconocidas distintas."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-a", "uuid-b"], "unit": ["ml", "oz"]}),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert {"ml", "oz"}.issubset(set(report.unknown_units["unit"]))


# ---------------------------------------------------------------------------
# SensorLoader.validate - Row filtering (filas inválidas se eliminan)
# ---------------------------------------------------------------------------


class TestValidateFiltering:
    def test_removes_null_values(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con valores nulos."""
        df = sample_df.copy()
        df.loc[0, "id"] = None
        df.loc[1, "unit"] = None
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 2
        assert not report.null_values.empty

    def test_removes_blank_strings(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con cadenas en blanco (espacios)."""
        df = sample_df.copy()
        df.loc[0, "id"] = "   "
        df.loc[1, "id"] = ""
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 2

    def test_removes_duplicate_ids(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con IDs duplicados, manteniendo la primera."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-1"], "unit": ["kg"]}),
            ],
            ignore_index=True,
        )
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 1
        assert len(df[df["id"] == "uuid-1"]) == 1
        assert not report.duplicate_ids.empty

    def test_removes_invalid_units(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con unidades inválidas."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-99"], "unit": ["ml"]}),
            ],
            ignore_index=True,
        )
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 1
        assert not report.unknown_units.empty

    def test_dataframe_modified_inplace(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe modificar el dataframe original (in-place)."""
        df = sample_df.copy()
        df.loc[0, "id"] = None
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 1

    def test_valid_rows_remain(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe mantener filas válidas después de filtrar."""
        df = sample_df.copy()
        _ = loader.validate(df)
        assert len(df) == len(sample_df)
        assert set(df["id"]) == set(sample_df["id"])


# ---------------------------------------------------------------------------
# SensorLoader.save
# ---------------------------------------------------------------------------


class TestSave:
    def test_row_count_matches(
        self, loader: SensorLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """La tabla sensor debe contener el mismo número de filas."""
        loader.save(sample_df)
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM sensor").fetchone()[0]
        assert count == len(sample_df)

    def test_columns_stored_correctly(
        self, loader: SensorLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """Las columnas almacenadas deben ser id y unit."""
        loader.save(sample_df)
        with sqlite3.connect(db_path) as con:
            cols = [
                d[0] for d in con.execute("SELECT * FROM sensor LIMIT 1").description
            ]
        assert "id" in cols
        assert "unit" in cols

    def test_idempotent_on_duplicate(
        self, loader: SensorLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """Llamar save() dos veces no debe duplicar filas (409 silenciado)."""
        loader.save(sample_df)
        loader.save(sample_df)
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM sensor").fetchone()[0]
        assert count == len(sample_df)

    def test_returns_row_count(
        self, loader: SensorLoader, sample_df: pd.DataFrame
    ) -> None:
        """save() debe devolver el número de filas del DataFrame."""
        assert loader.save(sample_df) == len(sample_df)

    def test_unknown_unit_not_stored(
        self, loader: SensorLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """Sensores con unidades inválidas son rechazados por la API (422) y no se almacenan."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame({"id": ["uuid-invalid"], "unit": ["ml"]}),
            ],
            ignore_index=True,
        )
        loader.save(df)
        with sqlite3.connect(db_path) as con:
            count = con.execute(
                "SELECT COUNT(*) FROM sensor WHERE id='uuid-invalid'"
            ).fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# SensorLoader.run (pipeline completo)
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_returns_rows_and_report(self, loader: SensorLoader) -> None:
        """run() debe devolver (int, SensorValidationReport)."""
        rows, report = loader.run()
        assert isinstance(rows, int)
        assert isinstance(report, SensorValidationReport)

    def test_run_row_count(self, loader: SensorLoader, sample_df: pd.DataFrame) -> None:
        """run() debe procesar el mismo número de filas que hay en el parquet."""
        rows, _ = loader.run()
        assert rows == len(sample_df)

    def test_run_with_real_parquet(self, tmp_path: Path) -> None:
        """run() debe procesar el parquet real del proyecto sin errores."""
        real = Path("data/sensors.parquet")
        if not real.exists():
            pytest.skip("data/sensors.parquet no disponible")

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
                loader = SensorLoader(parquet_path=real, http_client=client)
                rows, report = loader.run()
        finally:
            app.dependency_overrides.clear()

        assert rows == 200
        assert report.null_values.empty
        assert report.duplicate_ids.empty
        assert report.unknown_units.empty
