"""Tests for CowLoader in loader.py."""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app, get_db
from init_db import init_db
from loader import CowLoader, CowValidationReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """Clean dataset with no anomalies."""
    return pd.DataFrame(
        {
            "id": ["uuid-1", "uuid-2", "uuid-3"],
            "name": ["Bessie #1", "Daisy #2", "Molly #3"],
            "birthdate": pd.to_datetime(["2020-01-01", "2021-03-15", "2019-07-20"]),
        }
    )


@pytest.fixture()
def parquet_file(sample_df: pd.DataFrame, tmp_path: Path) -> Path:
    """Write sample_df to a temporary parquet file and return its path."""
    path = tmp_path / "cows.parquet"
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
def loader(parquet_file: Path, http_client) -> CowLoader:
    return CowLoader(parquet_path=parquet_file, http_client=http_client)


# ---------------------------------------------------------------------------
# CowLoader.load
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_dataframe(self, loader: CowLoader) -> None:
        """load() debe devolver un DataFrame."""
        df = loader.load()
        assert isinstance(df, pd.DataFrame)

    def test_expected_columns(self, loader: CowLoader) -> None:
        """El DataFrame debe tener las columnas id, name y birthdate."""
        df = loader.load()
        assert set(df.columns) == {"id", "name", "birthdate"}

    def test_row_count(self, loader: CowLoader, sample_df: pd.DataFrame) -> None:
        """El número de filas debe coincidir con el parquet original."""
        df = loader.load()
        assert len(df) == len(sample_df)

    def test_birthdate_parsed_as_datetime(self, loader: CowLoader) -> None:
        """birthdate se debe parsear como datetime."""
        df = loader.load()
        assert pd.api.types.is_datetime64_any_dtype(df["birthdate"])

    def test_file_not_found_raises(self, http_client) -> None:
        """load() debe lanzar FileNotFoundError si el parquet no existe."""
        loader = CowLoader(
            parquet_path=Path("nonexistent.parquet"), http_client=http_client
        )
        with pytest.raises(FileNotFoundError):
            loader.load()


# ---------------------------------------------------------------------------
# CowLoader.validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_clean_data_no_anomalies(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """Un dataset limpio no debe reportar anomalías."""
        report = loader.validate(sample_df)
        assert report.duplicate_names.empty
        assert report.duplicate_ids.empty
        assert report.future_birthdates.empty

    def test_detects_duplicate_names(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar vacas con el mismo nombre."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-99"],
                        "name": ["Bessie #1"],
                        "birthdate": pd.to_datetime(["2022-01-01"]),
                    }
                ),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert not report.duplicate_names.empty
        assert set(report.duplicate_names["name"]) == {"Bessie #1"}

    def test_detects_duplicate_ids(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar vacas con id duplicado."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-1"],
                        "name": ["Clone #1"],
                        "birthdate": pd.to_datetime(["2022-01-01"]),
                    }
                ),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert not report.duplicate_ids.empty
        assert set(report.duplicate_ids["id"]) == {"uuid-1"}

    def test_duplicate_ids_deduplicates_report(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """duplicate_ids debe contener una sola fila por id duplicado (sin repetir el valor)."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-1", "uuid-1"],
                        "name": ["Clone A", "Clone B"],
                        "birthdate": pd.to_datetime(["2022-01-01", "2023-01-01"]),
                    }
                ),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert len(report.duplicate_ids) == 1

    def test_detects_future_birthdates(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe detectar fechas de nacimiento en el futuro."""
        future = pd.Timestamp.today() + timedelta(days=30)
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-99"],
                        "name": ["Future #1"],
                        "birthdate": [future],
                    }
                ),
            ],
            ignore_index=True,
        )
        report = loader.validate(df)
        assert not report.future_birthdates.empty
        assert "uuid-99" in report.future_birthdates["id"].values

    def test_returns_validation_report_type(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe devolver una instancia de CowValidationReport."""
        report = loader.validate(sample_df)
        assert isinstance(report, CowValidationReport)


# ---------------------------------------------------------------------------
# CowLoader.validate - Row filtering (filas inválidas se eliminan)
# ---------------------------------------------------------------------------


class TestValidateFiltering:
    def test_removes_duplicate_ids(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con IDs duplicados, manteniendo la primera."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-1"],
                        "name": ["Clone #1"],
                        "birthdate": pd.to_datetime(["2022-01-01"]),
                    }
                ),
            ],
            ignore_index=True,
        )
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 1
        assert len(df[df["id"] == "uuid-1"]) == 1
        assert not report.duplicate_ids.empty

    def test_removes_duplicate_names(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con nombres duplicados, manteniendo la primera."""
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-99"],
                        "name": ["Bessie #1"],
                        "birthdate": pd.to_datetime(["2022-01-01"]),
                    }
                ),
            ],
            ignore_index=True,
        )
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 1
        assert len(df[df["name"] == "Bessie #1"]) == 1
        assert not report.duplicate_names.empty

    def test_removes_future_birthdates(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con fechas de nacimiento en el futuro."""
        future = pd.Timestamp.today() + timedelta(days=30)
        df = pd.concat(
            [
                sample_df,
                pd.DataFrame(
                    {
                        "id": ["uuid-99"],
                        "name": ["Future #1"],
                        "birthdate": [future],
                    }
                ),
            ],
            ignore_index=True,
        )
        initial_count = len(df)
        report = loader.validate(df)
        assert len(df) == initial_count - 1
        assert not report.future_birthdates.empty

    def test_removes_null_values(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con valores nulos en campos críticos."""
        df = sample_df.copy()
        df.loc[0, "id"] = None
        df.loc[1, "name"] = None
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 2

    def test_removes_blank_strings(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe eliminar filas con cadenas en blanco (espacios)."""
        df = sample_df.copy()
        df.loc[0, "id"] = "   "
        df.loc[1, "name"] = ""
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 2

    def test_dataframe_modified_inplace(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe modificar el dataframe original (in-place)."""
        df = sample_df.copy()
        df.loc[0, "id"] = None
        initial_count = len(df)
        _ = loader.validate(df)
        assert len(df) == initial_count - 1
        assert df.iloc[0]["id"] is not None or len(df) < initial_count

    def test_valid_rows_remain(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """validate() debe mantener filas válidas después de filtrar."""
        df = sample_df.copy()
        _ = loader.validate(df)
        assert len(df) == len(sample_df)
        assert set(df["id"]) == set(sample_df["id"])


# ---------------------------------------------------------------------------
# CowLoader.save
# ---------------------------------------------------------------------------


class TestSave:
    def test_row_count_matches(
        self, loader: CowLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """La tabla cow debe contener el mismo número de filas que el DataFrame."""
        loader.save(sample_df)
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM cow").fetchone()[0]
        assert count == len(sample_df)

    def test_columns_stored_correctly(
        self, loader: CowLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """Las columnas almacenadas deben ser id, name y birthdate."""
        loader.save(sample_df)
        with sqlite3.connect(db_path) as con:
            cols = [d[0] for d in con.execute("SELECT * FROM cow LIMIT 1").description]
        assert "id" in cols
        assert "name" in cols
        assert "birthdate" in cols

    def test_idempotent_on_duplicate(
        self, loader: CowLoader, sample_df: pd.DataFrame, db_path: Path
    ) -> None:
        """Llamar save() dos veces no debe duplicar filas (409 silenciado)."""
        loader.save(sample_df)
        loader.save(sample_df)
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM cow").fetchone()[0]
        assert count == len(sample_df)

    def test_returns_row_count(
        self, loader: CowLoader, sample_df: pd.DataFrame
    ) -> None:
        """save() debe devolver el número de filas del DataFrame."""
        result = loader.save(sample_df)
        assert result == len(sample_df)


# ---------------------------------------------------------------------------
# CowLoader.run (pipeline completo)
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_returns_rows_and_report(self, loader: CowLoader) -> None:
        """run() debe devolver (int, CowValidationReport)."""
        rows, report = loader.run()
        assert isinstance(rows, int)
        assert isinstance(report, CowValidationReport)

    def test_run_with_real_parquet(self, tmp_path: Path) -> None:
        """run() debe procesar el parquet real del proyecto sin errores."""
        real_parquet = Path("data/cows.parquet")
        if not real_parquet.exists():
            pytest.skip("data/cows.parquet no disponible")

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
                loader = CowLoader(parquet_path=real_parquet, http_client=client)
                rows, report = loader.run()
        finally:
            app.dependency_overrides.clear()

        assert rows == 125  # 128 original - 3 duplicados eliminados
        assert not report.duplicate_names.empty
