from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from app.profiling.types import LogicalType

DATETIME_PARSE_THRESHOLD = 0.80
NUMERIC_PARSE_THRESHOLD = 0.80
CATEGORICAL_CARDINALITY_RATIO = 0.50
CATEGORICAL_MAX_UNIQUE = 50
BOOLEAN_VALUES = frozenset({"true", "false", "yes", "no", "0", "1", "y", "n"})


class SchemaMappingError(ValueError):
    """Raised when schema inference cannot proceed."""


def _normalize_column_name(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def _coerce_numeric_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    cleaned = (
        series.astype(str)
        .str.replace(r"[\$,]", "", regex=True)
        .str.replace(r"\(([^)]+)\)", r"-\1", regex=True)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _parse_ratio(success_count: int, total_count: int) -> float:
    if total_count == 0:
        return 0.0
    return success_count / total_count


def _infer_boolean(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False

    normalized = non_null.astype(str).str.strip().str.lower()
    unique = set(normalized.unique())
    if not unique.issubset(BOOLEAN_VALUES):
        return False
    return len(unique) <= 2


def _infer_datetime(series: pd.Series) -> tuple[bool, pd.Series | None]:
    if pd.api.types.is_numeric_dtype(series):
        return False, None

    if pd.api.types.is_datetime64_any_dtype(series):
        return True, series

    non_null = series.dropna()
    if non_null.empty:
        return False, None

    parsed = pd.to_datetime(non_null, errors="coerce", utc=False, format="mixed")
    ratio = _parse_ratio(int(parsed.notna().sum()), len(non_null))
    if ratio < DATETIME_PARSE_THRESHOLD:
        return False, None

    # Reject epoch-nanosecond false positives from numeric strings like "100.5".
    if parsed.notna().all() and parsed.dt.year.eq(1970).all():
        max_span = (parsed.max() - parsed.min()).total_seconds()
        if max_span < 86400:
            return False, None

    return True, parsed


def _infer_numeric(series: pd.Series) -> tuple[bool, pd.Series | None]:
    if pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        return True, numeric

    non_null_count = int(series.notna().sum())
    if non_null_count == 0:
        return False, None

    numeric = _coerce_numeric_series(series)
    ratio = _parse_ratio(int(numeric.notna().sum()), non_null_count)
    if ratio >= NUMERIC_PARSE_THRESHOLD:
        return True, numeric
    return False, None


def _infer_categorical(series: pd.Series) -> bool:
    non_null = series.dropna().astype(str)
    if non_null.empty:
        return False

    unique_count = non_null.nunique()
    if unique_count <= 1:
        return False

    ratio = unique_count / len(non_null)
    return unique_count <= CATEGORICAL_MAX_UNIQUE and ratio <= CATEGORICAL_CARDINALITY_RATIO


def infer_logical_type(series: pd.Series) -> tuple[LogicalType, pd.Series]:
    """Infer the logical type of a column and return a normalized series."""
    if series.isna().all():
        return LogicalType.UNKNOWN, series

    if _infer_boolean(series):
        normalized = series.map(
            lambda value: str(value).strip().lower() in {"true", "yes", "1", "y"}
            if pd.notna(value)
            else np.nan
        )
        return LogicalType.BOOLEAN, normalized

    is_datetime, datetime_series = _infer_datetime(series)
    if is_datetime and datetime_series is not None:
        full = pd.Series(index=series.index, dtype="datetime64[ns]")
        full.loc[datetime_series.index] = datetime_series
        return LogicalType.DATETIME, full

    is_numeric, numeric_series = _infer_numeric(series)
    if is_numeric and numeric_series is not None:
        non_null = numeric_series.dropna()
        if not non_null.empty and (non_null % 1 == 0).all():
            return LogicalType.INTEGER, numeric_series
        if pd.api.types.is_integer_dtype(non_null):
            return LogicalType.INTEGER, numeric_series
        return LogicalType.FLOAT, numeric_series

    if _infer_categorical(series):
        return LogicalType.CATEGORICAL, series.astype(str)

    return LogicalType.STRING, series.astype(str)


def map_schema(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, LogicalType]]:
    """
    Map raw dataframe columns to logical types and return a typed copy.

    Raises:
        SchemaMappingError: If the dataframe has no columns or invalid structure.
    """
    if df is None:
        raise SchemaMappingError("Dataset is None.")
    if df.shape[1] == 0:
        raise SchemaMappingError("Dataset has no columns.")

    typed_df = df.copy()
    logical_types: dict[str, LogicalType] = {}

    for column in typed_df.columns:
        column_name = str(column)
        series = typed_df[column_name]
        logical_type, normalized = infer_logical_type(series)
        logical_types[column_name] = logical_type
        typed_df[column_name] = normalized

    return typed_df, logical_types


def sample_values(series: pd.Series, limit: int = 5) -> list[Any]:
    """Return JSON-serializable sample values from a column."""
    non_null = series.dropna()
    if non_null.empty:
        return []

    samples: list[Any] = []
    for value in non_null.head(limit).tolist():
        if isinstance(value, (np.integer, np.floating)):
            samples.append(value.item())
        elif isinstance(value, pd.Timestamp):
            samples.append(value.isoformat())
        elif pd.isna(value):
            continue
        else:
            samples.append(str(value))
    return samples
