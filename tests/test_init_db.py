"""Tests for init_db.py — TDD red phase."""

import sqlite3
from pathlib import Path

import pytest

from init_db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def db(db_path: Path) -> sqlite3.Connection:
    """Ejecuta init_db y devuelve una conexión abierta a la DB inicializada."""
    init_db(db_path)
    con = sqlite3.connect(db_path)
    yield con
    con.close()


# ---------------------------------------------------------------------------
# Creación de tablas
# ---------------------------------------------------------------------------


class TestTablesCreated:
    def test_creates_cow_table(self, db: sqlite3.Connection) -> None:
        """init_db debe crear la tabla cow."""
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "cow" in tables

    def test_creates_sensor_table(self, db: sqlite3.Connection) -> None:
        """init_db debe crear la tabla sensor."""
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sensor" in tables

    def test_creates_measurement_table(self, db: sqlite3.Connection) -> None:
        """init_db debe crear la tabla measurement."""
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "measurement" in tables


# ---------------------------------------------------------------------------
# Esquema de cow
# ---------------------------------------------------------------------------


class TestCowSchema:
    def test_cow_columns(self, db: sqlite3.Connection) -> None:
        """La tabla cow debe tener las columnas id, name y birthdate."""
        cols = {row[1] for row in db.execute("PRAGMA table_info(cow)").fetchall()}
        assert cols == {"id", "name", "birthdate"}

    def test_cow_primary_key(self, db: sqlite3.Connection) -> None:
        """La columna id debe ser PK de cow."""
        pk_cols = [
            row[1]
            for row in db.execute("PRAGMA table_info(cow)").fetchall()
            if row[5] == 1
        ]
        assert pk_cols == ["id"]

    def test_cow_rejects_duplicate_id(self, db: sqlite3.Connection) -> None:
        """cow no debe permitir dos filas con el mismo id."""
        db.execute("INSERT INTO cow VALUES ('x', 'Bessie', '2020-01-01')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO cow VALUES ('x', 'Daisy', '2021-01-01')")

    def test_cow_rejects_null_name(self, db: sqlite3.Connection) -> None:
        """cow no debe aceptar name nulo."""
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO cow VALUES ('x', NULL, '2020-01-01')")

    def test_cow_rejects_null_birthdate(self, db: sqlite3.Connection) -> None:
        """cow no debe aceptar birthdate nulo."""
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO cow VALUES ('x', 'Bessie', NULL)")


# ---------------------------------------------------------------------------
# Esquema de sensor
# ---------------------------------------------------------------------------


class TestSensorSchema:
    def test_sensor_columns(self, db: sqlite3.Connection) -> None:
        """La tabla sensor debe tener las columnas id y unit."""
        cols = {row[1] for row in db.execute("PRAGMA table_info(sensor)").fetchall()}
        assert cols == {"id", "unit"}

    def test_sensor_primary_key(self, db: sqlite3.Connection) -> None:
        """La columna id debe ser PK de sensor."""
        pk_cols = [
            row[1]
            for row in db.execute("PRAGMA table_info(sensor)").fetchall()
            if row[5] == 1
        ]
        assert pk_cols == ["id"]

    def test_sensor_rejects_duplicate_id(self, db: sqlite3.Connection) -> None:
        """sensor no debe permitir dos filas con el mismo id."""
        db.execute("INSERT INTO sensor VALUES ('s-1', 'L')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO sensor VALUES ('s-1', 'kg')")

    def test_sensor_rejects_null_unit(self, db: sqlite3.Connection) -> None:
        """sensor no debe aceptar unit nulo."""
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO sensor VALUES ('s-1', NULL)")


# ---------------------------------------------------------------------------
# Esquema de measurement
# ---------------------------------------------------------------------------


class TestMeasurementSchema:
    def test_measurement_columns(self, db: sqlite3.Connection) -> None:
        """La tabla measurement debe tener sensor_id, cow_id, timestamp y value."""
        cols = {
            row[1] for row in db.execute("PRAGMA table_info(measurement)").fetchall()
        }
        assert cols == {"sensor_id", "cow_id", "timestamp", "value"}

    def test_measurement_composite_pk(self, db: sqlite3.Connection) -> None:
        """measurement debe tener PK compuesta por sensor_id, cow_id y timestamp."""
        db.execute("INSERT INTO sensor VALUES ('s-1', 'L')")
        db.execute("INSERT INTO cow VALUES ('c-1', 'Bessie', '2020-01-01')")
        db.execute(
            "INSERT INTO measurement VALUES ('s-1', 'c-1', '2023-01-01 10:00:00', 4.72)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO measurement VALUES ('s-1', 'c-1', '2023-01-01 10:00:00', 9.99)"
            )

    def test_measurement_fk_sensor(self, db: sqlite3.Connection) -> None:
        """measurement debe rechazar sensor_id que no exista en sensor."""
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO cow VALUES ('c-1', 'Bessie', '2020-01-01')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO measurement VALUES ('no-existe', 'c-1', '2023-01-01 10:00:00', 4.72)"
            )

    def test_measurement_fk_cow(self, db: sqlite3.Connection) -> None:
        """measurement debe rechazar cow_id que no exista en cow."""
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO sensor VALUES ('s-1', 'L')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO measurement VALUES ('s-1', 'no-existe', '2023-01-01 10:00:00', 4.72)"
            )

    def test_measurement_fk_declared_in_ddl(self, db: sqlite3.Connection) -> None:
        """El DDL de measurement debe declarar ambas FK."""
        ddl = (
            db.execute("SELECT sql FROM sqlite_master WHERE name='measurement'")
            .fetchone()[0]
            .upper()
        )
        assert "FOREIGN KEY" in ddl
        assert "SENSOR" in ddl
        assert "COW" in ddl


# ---------------------------------------------------------------------------
# Índices
# ---------------------------------------------------------------------------


class TestIndexes:
    """Los índices de rendimiento deben existir tras llamar a init_db."""

    def _index_names(self, db: sqlite3.Connection) -> set[str]:
        return {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }

    def test_idx_measurement_cow_ts_exists(self, db: sqlite3.Connection) -> None:
        """Debe existir un índice sobre measurement(cow_id, timestamp)."""
        assert "idx_measurement_cow_ts" in self._index_names(db)

    def test_idx_measurement_sensor_ts_exists(self, db: sqlite3.Connection) -> None:
        """Debe existir un índice sobre measurement(sensor_id, timestamp)."""
        assert "idx_measurement_sensor_ts" in self._index_names(db)

    def test_idx_sensor_unit_exists(self, db: sqlite3.Connection) -> None:
        """Debe existir un índice sobre sensor(unit)."""
        assert "idx_sensor_unit" in self._index_names(db)

    def test_idx_measurement_cow_ts_covers_correct_table(
        self, db: sqlite3.Connection
    ) -> None:
        """idx_measurement_cow_ts debe estar definido sobre la tabla measurement."""
        row = db.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='idx_measurement_cow_ts'"
        ).fetchone()
        assert row is not None and row[0] == "measurement"

    def test_idx_measurement_sensor_ts_covers_correct_table(
        self, db: sqlite3.Connection
    ) -> None:
        """idx_measurement_sensor_ts debe estar definido sobre la tabla measurement."""
        row = db.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='idx_measurement_sensor_ts'"
        ).fetchone()
        assert row is not None and row[0] == "measurement"

    def test_idx_sensor_unit_covers_correct_table(
        self, db: sqlite3.Connection
    ) -> None:
        """idx_sensor_unit debe estar definido sobre la tabla sensor."""
        row = db.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='idx_sensor_unit'"
        ).fetchone()
        assert row is not None and row[0] == "sensor"

    def test_indexes_idempotent(self, db_path: Path) -> None:
        """Llamar init_db dos veces no debe fallar por índices duplicados."""
        init_db(db_path)
        init_db(db_path)


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_init_twice_does_not_raise(self, db_path: Path) -> None:
        """Llamar init_db dos veces no debe lanzar ninguna excepción."""
        init_db(db_path)
        init_db(db_path)

    def test_existing_data_preserved(self, db_path: Path) -> None:
        """Los datos existentes no deben borrarse al llamar init_db por segunda vez."""
        init_db(db_path)
        con = sqlite3.connect(db_path)
        con.execute("INSERT INTO cow VALUES ('c-1', 'Bessie', '2020-01-01')")
        con.commit()
        con.close()

        init_db(db_path)

        con = sqlite3.connect(db_path)
        count = con.execute("SELECT COUNT(*) FROM cow").fetchone()[0]
        con.close()
        assert count == 1
