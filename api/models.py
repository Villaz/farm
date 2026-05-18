"""Pydantic models shared by API endpoints."""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints, field_validator

from api.validation import VALID_UNITS

_Name = Annotated[str, StringConstraints(min_length=1, max_length=255)]
_Id = Annotated[str, StringConstraints(min_length=1, max_length=255)]


# ---------------------------------------------------------------------------
# Cow
# ---------------------------------------------------------------------------


class CowCreate(BaseModel):
    """Fields required to create a new cow."""

    name: _Name
    birthdate: date

    @field_validator("birthdate")
    @classmethod
    def birthdate_not_in_future(cls, v: date) -> date:
        """Reject birthdates that have not yet occurred."""
        if v > date.today():
            raise ValueError("birthdate cannot be in the future")
        return v


class CowResponse(BaseModel):
    """Representation of a cow returned by the API."""

    id: str
    name: str
    birthdate: date


class LatestMeasurement(BaseModel):
    """Latest sensor record associated with a cow."""

    sensor_id: str
    timestamp: datetime
    value: float
    unit: str


class CowDetailResponse(BaseModel):
    """Cow details with its latest sensor measurement."""

    id: str
    name: str
    birthdate: date
    latest_measurement: LatestMeasurement | None


# ---------------------------------------------------------------------------
# Sensor
# ---------------------------------------------------------------------------


class SensorCreate(BaseModel):
    """Fields required to create a new sensor."""

    unit: str

    @field_validator("unit")
    @classmethod
    def unit_must_be_valid(cls, v: str) -> str:
        """Validate that unit belongs to the set of accepted units."""
        if v not in VALID_UNITS:
            raise ValueError(
                f"Unit '{v}' is not valid. Accepted values: {sorted(VALID_UNITS)}"
            )
        return v


class SensorResponse(BaseModel):
    """Representation of a sensor returned by the API."""

    id: str
    unit: str


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


class MeasurementCreate(BaseModel):
    """Fields required to create a new measurement."""

    sensor_id: _Id
    cow_id: _Id
    timestamp: datetime
    value: float

    @field_validator("value")
    @classmethod
    def value_must_be_non_negative(cls, v: float) -> float:
        """Reject negative values (sensor error sentinel)."""
        if v < 0:
            raise ValueError("The 'value' field cannot be negative")
        return v


class MeasurementResponse(BaseModel):
    """Representation of a measurement returned by the API."""

    sensor_id: str
    cow_id: str
    timestamp: datetime
    value: float


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


class MilkProductionEntry(BaseModel):
    """Daily milk production of a cow."""

    cow_id: str
    cow_name: str
    date: date
    total_liters: float


class CowWeightSummary(BaseModel):
    """Current weight and 30-day average of a cow."""

    cow_id: str
    cow_name: str
    current_weight_kg: float | None
    avg_weight_30d_kg: float | None


class IllCowEntry(BaseModel):
    """Potentially ill cow with detected reasons."""

    cow_id: str
    cow_name: str
    reasons: list[str]


# ---------------------------------------------------------------------------
# Load responses
# ---------------------------------------------------------------------------


class CowLoadResponse(BaseModel):
    """Result of loading a cows parquet file."""

    rows_processed: int
    duplicate_names: int
    duplicate_ids: int
    future_birthdates: int


class SensorLoadResponse(BaseModel):
    """Result of loading a sensors parquet file."""

    rows_processed: int
    null_values: int
    duplicate_ids: int
    unknown_units: int


class MeasurementLoadResponse(BaseModel):
    """Result of loading a measurements parquet file."""

    rows_processed: int
    null_values: int
    negative_values: int
    future_timestamps: int
