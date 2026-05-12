"""Validation rules and data quality reports shared between the
loader and API endpoints.

- VALID_UNITS: single source of truth for accepted sensor units.
- ValidationReport dataclasses: used by loaders to report anomalies
  and by endpoints to apply the same domain rules.
"""

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

VALID_UNITS: frozenset[str] = frozenset({"L", "kg"})


# ---------------------------------------------------------------------------
# Validation reports
# ---------------------------------------------------------------------------


@dataclass
class CowValidationReport:
    """Summary of anomalous values detected in the cows dataset."""

    duplicate_names: pd.DataFrame
    """Rows with duplicate 'name'; one row per unique duplicate value."""

    duplicate_ids: pd.DataFrame
    """Rows with duplicate 'id'; one row per unique duplicate value."""

    future_birthdates: pd.DataFrame
    """Rows with 'birthdate' after the current date."""

    def log_summary(self) -> None:
        """Emit a summary of detected anomalies to logs."""
        if not self.duplicate_names.empty:
            logger.warning(
                "Duplicate names (%d values): %s",
                len(self.duplicate_names),
                self.duplicate_names["name"].tolist(),
            )
        if not self.duplicate_ids.empty:
            logger.warning(
                "Duplicate IDs (%d values): %s",
                len(self.duplicate_ids),
                self.duplicate_ids["id"].tolist(),
            )
        if not self.future_birthdates.empty:
            logger.warning(
                "Future birthdates (%d rows): ids=%s",
                len(self.future_birthdates),
                self.future_birthdates["id"].tolist(),
            )
        if (
            self.duplicate_names.empty
            and self.duplicate_ids.empty
            and self.future_birthdates.empty
        ):
            logger.info("Validation completed: no anomalies detected.")


@dataclass
class MeasurementValidationReport:
    """Summary of anomalous values detected in the measurements dataset."""

    null_values: pd.DataFrame
    """Rows whose 'value' field is null."""

    negative_values: pd.DataFrame
    """Rows whose 'value' field is negative (sensor error sentinel)."""

    future_timestamps: pd.DataFrame
    """Rows with 'timestamp' after the current instant."""

    def log_summary(self) -> None:
        """Emit a summary of detected anomalies to logs."""
        if not self.null_values.empty:
            logger.warning("Null values in 'value': %d rows", len(self.null_values))
        if not self.negative_values.empty:
            logger.warning(
                "Negative values in 'value': %d rows (min=%.2f)",
                len(self.negative_values),
                self.negative_values["value"].min(),
            )
        if not self.future_timestamps.empty:
            logger.warning("Future timestamps: %d rows", len(self.future_timestamps))
        if (
            self.null_values.empty
            and self.negative_values.empty
            and self.future_timestamps.empty
        ):
            logger.info("Validation completed: no anomalies detected.")


@dataclass
class SensorValidationReport:
    """Summary of anomalous values detected in the sensors dataset."""

    null_values: pd.DataFrame
    """Rows containing any null value in any column."""

    duplicate_ids: pd.DataFrame
    """Rows with duplicate 'id'; one row per unique duplicate value."""

    unknown_units: pd.DataFrame
    """Rows whose 'unit' does not belong to the set of valid units (VALID_UNITS)."""

    def log_summary(self) -> None:
        """Emit a summary of detected anomalies to logs."""
        if not self.null_values.empty:
            logger.warning("Null values: %d rows", len(self.null_values))
        if not self.duplicate_ids.empty:
            logger.warning(
                "Duplicate IDs (%d rows): %s",
                len(self.duplicate_ids),
                self.duplicate_ids["id"].unique().tolist(),
            )
        if not self.unknown_units.empty:
            logger.warning(
                "Unknown units (%d rows): %s",
                len(self.unknown_units),
                self.unknown_units["unit"].unique().tolist(),
            )
        if (
            self.null_values.empty
            and self.duplicate_ids.empty
            and self.unknown_units.empty
        ):
            logger.info("Validation completed: no anomalies detected.")
