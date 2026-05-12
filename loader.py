"""Loads data from Parquet files and persists it via the REST API.

Available classes:
- CowLoader         → POST /cows/{id}
- MeasurementLoader → POST /measurements
- SensorLoader      → POST /sensors/{id}
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Generic, TypeVar

import httpx
import pandas as pd

from api.validation import (
    VALID_UNITS,
    CowValidationReport,
    MeasurementValidationReport,
    SensorValidationReport,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

R = TypeVar("R")


class BaseLoader(ABC, Generic[R]):
    """Base class that defines the load → validate → save pipeline.

    Subclasses must implement `load()` and `validate()` and declare
    the class attribute `table_name` with the resource name.

    Args:
        parquet_path: Path to the source Parquet file.
        base_url: Base URL of the REST API (ignored if http_client is provided).
        http_client: Pre-configured HTTP client (useful in tests for injecting TestClient).
    """

    table_name: ClassVar[str]

    def __init__(
        self,
        parquet_path: str | Path,
        base_url: str = "http://localhost:8000",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.parquet_path = Path(parquet_path)
        # Use 60s timeout by default; increase if needed for large operations
        self._client = http_client or httpx.Client(base_url=base_url, timeout=60.0)

    def _read_parquet(self) -> pd.DataFrame:
        """Read the parquet file, verifying that it exists.

        Raises:
            FileNotFoundError: If the Parquet file does not exist.
        """
        if not self.parquet_path.exists():
            raise FileNotFoundError(f"File not found: {self.parquet_path}")
        df = pd.read_parquet(self.parquet_path)
        logger.info("Parquet loaded: %d rows, %d columns", *df.shape)
        return df

    @abstractmethod
    def load(self) -> pd.DataFrame:
        """Read and prepare the DataFrame from the Parquet file."""

    @abstractmethod
    def validate(self, df: pd.DataFrame) -> R:
        """Detect anomalous values and return the corresponding report."""

    @abstractmethod
    def save(self, df: pd.DataFrame) -> int:
        """Send each row of the DataFrame to the corresponding API endpoint.

        Returns:
            Total number of rows processed (including duplicates and validation errors).
        """

    def run(self) -> tuple[int, R]:
        """Execute the complete pipeline: load → validate → persist.

        Returns:
            Tuple (rows_processed, validation_report).
        """
        df = self.load()
        report = self.validate(df)
        rows = self.save(df)
        return rows, report


# ---------------------------------------------------------------------------
# Concrete loaders
# ---------------------------------------------------------------------------


class CowLoader(BaseLoader[CowValidationReport]):
    """Load, validate and persist the cows dataset via POST /cows/{id}."""

    table_name: ClassVar[str] = "cow"

    def load(self) -> pd.DataFrame:
        """Read the parquet file and convert 'birthdate' to datetime.

        Returns:
            DataFrame with columns id, name and birthdate.
        """
        df = self._read_parquet()
        df["birthdate"] = pd.to_datetime(df["birthdate"])
        return df

    def validate(self, df: pd.DataFrame) -> CowValidationReport:
        """Detect duplicate IDs, duplicate names, and future birthdates.

        Also removes rows from the dataframe that fail validation:
        - Null or blank IDs
        - Null or blank names
        - Future dates
        - Duplicate IDs (keeps first occurrence)
        - Duplicate names (keeps first occurrence)
        """
        report = CowValidationReport(
            duplicate_names=df[df.duplicated("name", keep=False)]
            .drop_duplicates("name")
            .copy(),
            duplicate_ids=df[df.duplicated("id", keep=False)]
            .drop_duplicates("id")
            .copy(),
            future_birthdates=df[df["birthdate"] > pd.Timestamp.today()].copy(),
        )

        valid_mask = (
            (df["id"].notna())
            & (df["id"].astype(str).str.strip().ne(""))
            & (df["name"].notna())
            & (df["name"].astype(str).str.strip().ne(""))
            & (df["birthdate"].notna())
            & (df["birthdate"] <= pd.Timestamp.today())
            & ~df.duplicated(subset=["id"], keep="first")
            & ~df.duplicated(subset=["name"], keep="first")
        )
        df.drop(df[~valid_mask].index, inplace=True)

        report.log_summary()
        return report

    def save(self, df: pd.DataFrame) -> int:
        """Send each cow to the POST /cows/{id} endpoint.

        HTTP 201 (created) and 409 (already exists) responses are considered successful.
        Any other status code is logged as a warning.
        """
        for _, row in df.iterrows():
            r = self._client.post(
                f"/cows/{row['id']}",
                json={
                    "name": row["name"],
                    "birthdate": str(pd.Timestamp(row["birthdate"]).date()),
                },
            )
            if r.status_code not in (201, 409):
                logger.warning(
                    "POST /cows/%s → %d: %s", row["id"], r.status_code, r.text
                )
        logger.info("'cow': %d rows processed.", len(df))
        return len(df)


class MeasurementLoader(BaseLoader[MeasurementValidationReport]):
    """Load, validate and persist the measurements dataset via POST /measurements."""

    table_name: ClassVar[str] = "measurement"

    def load(self) -> pd.DataFrame:
        """Read the parquet file and convert 'timestamp' from Unix epoch to datetime.

        Returns:
            DataFrame with columns sensor_id, cow_id, timestamp and value.
        """
        df = self._read_parquet()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        return df

    def validate(self, df: pd.DataFrame) -> MeasurementValidationReport:
        """Detect null values, negative values, and future timestamps.

        Also removes rows from the dataframe that fail validation:
        - Null values in value, sensor_id, cow_id, timestamp
        - Negative values in value
        - Future timestamps (timestamp > now)
        - Duplicate composite PK (sensor_id, cow_id, timestamp) - keeps first occurrence
        """
        ts = df["timestamp"]
        if not pd.api.types.is_datetime64_any_dtype(ts):
            ts = pd.to_datetime(ts, unit="s")

        report = MeasurementValidationReport(
            null_values=df[df["value"].isnull()].copy(),
            negative_values=df[df["value"] < 0].copy(),
            future_timestamps=df[ts > pd.Timestamp.now()].copy(),
        )

        valid_mask = (
            (df["value"].notna())
            & (df["value"] >= 0)
            & (df["sensor_id"].notna())
            & (df["cow_id"].notna())
            & (ts.notna())
            & (ts <= pd.Timestamp.now())
            & ~df.duplicated(subset=["sensor_id", "cow_id", "timestamp"], keep="first")
        )
        df.drop(df[~valid_mask].index, inplace=True)

        report.log_summary()
        return report

    def save(self, df: pd.DataFrame) -> int:
        """Send the DataFrame to the POST /load/measurements endpoint as a Parquet file.

        Converts datetime timestamps back to Unix epoch (seconds) before serialization,
        since the loading endpoint uses MeasurementLoader.load() internally,
        which expects timestamps in that format.
        """
        import io

        df_out = df.copy()
        if pd.api.types.is_datetime64_any_dtype(df_out["timestamp"]):
            ts = df_out["timestamp"]
            # Convert to Unix epoch seconds, handling both nanosecond and microsecond precision
            if ts.dtype == "datetime64[ns]":
                # datetime64[ns] → int64 nanoseconds → divide by 1e9 → seconds
                df_out["timestamp"] = ts.astype("int64") // 10**9
            else:
                # datetime64[us] or other → convert to datetime64[ns] first
                df_out["timestamp"] = (
                    ts.astype("datetime64[ns]").astype("int64") // 10**9
                )

        buf = io.BytesIO()
        df_out.to_parquet(buf, index=False)
        buf.seek(0)

        # Use extended timeout for large measurement uploads (up to 5 minutes)
        r = self._client.post(
            "/load/measurements",
            files={"file": ("measurements.parquet", buf, "application/octet-stream")},
            timeout=300.0,
        )
        if r.status_code != 200:
            logger.warning("POST /load/measurements → %d: %s", r.status_code, r.text)

        logger.info("'measurement': %d rows processed.", len(df))
        return len(df)


class SensorLoader(BaseLoader[SensorValidationReport]):
    """Load, validate and persist the sensors dataset via POST /sensors/{id}.

    Args:
        parquet_path: Path to the source Parquet file.
        base_url: Base URL of the REST API.
        http_client: Pre-configured HTTP client.
        valid_units: Set of accepted measurement units.
    """

    table_name: ClassVar[str] = "sensor"

    def __init__(
        self,
        parquet_path: str | Path,
        base_url: str = "http://localhost:8000",
        http_client: httpx.Client | None = None,
        valid_units: frozenset[str] = VALID_UNITS,
    ) -> None:
        super().__init__(parquet_path, base_url, http_client)
        self.valid_units = valid_units

    def load(self) -> pd.DataFrame:
        """Read the parquet file and return the DataFrame without additional transformations.

        Returns:
            DataFrame with columns id and unit.
        """
        return self._read_parquet()

    def validate(self, df: pd.DataFrame) -> SensorValidationReport:
        """Detect null values, duplicate IDs, and unknown units.

        Also removes rows from the dataframe that fail validation:
        - Null values in any column
        - Blank IDs (whitespace)
        - Blank names (whitespace)
        - Invalid units (not in VALID_UNITS)
        - Duplicate IDs (keeps first occurrence)
        """
        report = SensorValidationReport(
            null_values=df[df.isnull().any(axis=1)].copy(),
            duplicate_ids=df[df.duplicated("id", keep=False)]
            .drop_duplicates("id")
            .copy(),
            unknown_units=df[~df["unit"].isin(self.valid_units)].copy(),
        )

        valid_mask = (
            (df.notna().all(axis=1))
            & (df["id"].astype(str).str.strip().ne(""))
            & (df["unit"].isin(self.valid_units))
            & ~df.duplicated(subset=["id"], keep="first")
        )
        df.drop(df[~valid_mask].index, inplace=True)

        report.log_summary()
        return report

    def save(self, df: pd.DataFrame) -> int:
        """Send each sensor to the POST /sensors/{id} endpoint.

        HTTP 201, 409 and 422 responses are considered expected.
        """
        for _, row in df.iterrows():
            r = self._client.post(
                f"/sensors/{row['id']}",
                json={"unit": row["unit"]},
            )
            if r.status_code not in (201, 409, 422):
                logger.warning(
                    "POST /sensors/%s → %d: %s", row["id"], r.status_code, r.text
                )
        logger.info("'sensor': %d rows processed.", len(df))
        return len(df)


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    base_url = "http://localhost:8000"
    for loader in [
        CowLoader("data/cows.parquet", base_url=base_url),
        SensorLoader("data/sensors.parquet", base_url=base_url),
        MeasurementLoader("data/measurements.parquet", base_url=base_url),
    ]:
        inserted, _ = loader.run()
        print(f"{loader.table_name}: {inserted} rows processed")
