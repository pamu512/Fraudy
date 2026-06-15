from __future__ import annotations

import logging

import pandas as pd

from app.profiling.correlation_matrix import CorrelationMatrixError, compute_correlation_report
from app.profiling.descriptive_stats import compute_descriptive_stats
from app.profiling.identifier_detector import detect_identifiers
from app.profiling.loader import DatasetLoadError, load_dataset
from app.profiling.schema_mapper import SchemaMappingError, map_schema, sample_values
from app.profiling.types import ColumnProfile, DatasetProfile

logger = logging.getLogger(__name__)


class ProfilingError(RuntimeError):
    """Raised when dataset profiling fails."""


def profile_dataframe(
    df: pd.DataFrame,
    source_filename: str | None = None,
) -> DatasetProfile:
    """
    Profile a raw dataframe: infer schema, detect identifiers, compute stats
    and correlation matrices.
    """
    if df is None:
        raise ProfilingError("Dataset is None.")
    if df.empty:
        raise ProfilingError("Dataset contains no rows.")
    if df.shape[1] == 0:
        raise ProfilingError("Dataset contains no columns.")

    warnings: list[str] = []

    try:
        typed_df, logical_types = map_schema(df)
    except SchemaMappingError as exc:
        raise ProfilingError(str(exc)) from exc

    row_count = len(typed_df)
    columns: list[ColumnProfile] = []

    for column in typed_df.columns:
        column_name = str(column)
        series = typed_df[column_name]
        logical_type = logical_types[column_name]
        non_null = int(series.notna().sum())
        null_count = row_count - non_null
        null_pct = null_count / row_count if row_count else 0.0

        try:
            identifier_flags = detect_identifiers(column_name, logical_type, series)
        except Exception as exc:
            logger.warning("Identifier detection failed for column %s: %s", column_name, exc)
            identifier_flags = []
            warnings.append(f"Identifier detection skipped for column '{column_name}'.")

        try:
            descriptive = compute_descriptive_stats(logical_type, series)
        except Exception as exc:
            logger.warning("Descriptive stats failed for column %s: %s", column_name, exc)
            descriptive = {"note": f"Statistics unavailable: {exc}"}
            warnings.append(f"Descriptive statistics skipped for column '{column_name}'.")

        columns.append(
            ColumnProfile(
                name=column_name,
                logical_type=logical_type,
                pandas_dtype=str(series.dtype),
                null_count=null_count,
                null_pct=null_pct,
                unique_count=int(series.nunique(dropna=True)),
                sample_values=sample_values(series),
                identifier_flags=identifier_flags,
                descriptive_stats=descriptive,
            )
        )

    try:
        correlations = compute_correlation_report(typed_df, logical_types)
    except CorrelationMatrixError as exc:
        logger.warning("Correlation analysis failed: %s", exc)
        correlations = compute_correlation_report(
            typed_df.iloc[:, :0],
            {},
        )
        correlations.warnings.append(str(exc))
        warnings.append("Correlation analysis could not be completed.")

    warnings.extend(correlations.warnings)

    return DatasetProfile(
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        correlations=correlations,
        source_filename=source_filename,
        warnings=warnings,
    )


def profile_upload(content: bytes, filename: str) -> DatasetProfile:
    """Load and profile an uploaded raw dataset file."""
    try:
        df = load_dataset(content, filename)
    except DatasetLoadError as exc:
        raise ProfilingError(str(exc)) from exc

    return profile_dataframe(df, source_filename=filename)
