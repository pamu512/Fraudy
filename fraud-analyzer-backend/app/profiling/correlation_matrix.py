from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from app.profiling.types import CorrelationPair, CorrelationReport, LogicalType

MIN_NUMERIC_COLUMNS = 2
MIN_PAIR_SAMPLE = 3
STRONG_CORRELATION_THRESHOLD = 0.70
CONSTANT_COLUMN_STD = 1e-12


class CorrelationMatrixError(ValueError):
    """Raised when correlation analysis cannot be performed."""


def _select_numeric_columns(
    df: pd.DataFrame,
    logical_types: dict[str, LogicalType],
) -> list[str]:
    numeric_columns: list[str] = []
    for column in df.columns:
        logical_type = logical_types.get(str(column))
        if logical_type in {LogicalType.INTEGER, LogicalType.FLOAT}:
            numeric_columns.append(str(column))
    return numeric_columns


def _build_numeric_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    numeric_df = pd.DataFrame(index=df.index)
    for column in columns:
        numeric_df[column] = pd.to_numeric(df[column], errors="coerce")
    return numeric_df


def _matrix_to_json(matrix: pd.DataFrame) -> list[list[float | None]]:
    rows: list[list[float | None]] = []
    for _, row in matrix.iterrows():
        json_row: list[float | None] = []
        for value in row.tolist():
            if value is None or (isinstance(value, float) and not np.isfinite(value)):
                json_row.append(None)
            else:
                json_row.append(round(float(value), 4))
        rows.append(json_row)
    return rows


def _compute_pearson_matrix(numeric_df: pd.DataFrame) -> pd.DataFrame:
    return numeric_df.corr(method="pearson", min_periods=MIN_PAIR_SAMPLE)


def _compute_spearman_matrix(numeric_df: pd.DataFrame) -> pd.DataFrame:
    columns = list(numeric_df.columns)
    size = len(columns)
    values = np.full((size, size), np.nan, dtype=float)

    for i, col_a in enumerate(columns):
        values[i, i] = 1.0
        series_a = numeric_df[col_a]
        if series_a.dropna().std(ddof=1) <= CONSTANT_COLUMN_STD:
            continue

        for j in range(i + 1, size):
            col_b = columns[j]
            series_b = numeric_df[col_b]
            if series_b.dropna().std(ddof=1) <= CONSTANT_COLUMN_STD:
                continue

            aligned = pd.concat([series_a, series_b], axis=1).dropna()
            if len(aligned) < MIN_PAIR_SAMPLE:
                continue

            try:
                result = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
                coefficient = float(result.correlation)
            except (ValueError, FloatingPointError):
                continue

            if np.isfinite(coefficient):
                values[i, j] = coefficient
                values[j, i] = coefficient

    return pd.DataFrame(values, index=columns, columns=columns)


def _extract_strong_pairs(
    pearson: pd.DataFrame,
    spearman: pd.DataFrame,
    threshold: float = STRONG_CORRELATION_THRESHOLD,
) -> list[CorrelationPair]:
    pairs: list[CorrelationPair] = []
    columns = list(pearson.columns)

    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            col_a = columns[i]
            col_b = columns[j]

            pearson_value = pearson.loc[col_a, col_b]
            spearman_value = spearman.loc[col_a, col_b]

            pearson_abs = abs(pearson_value) if np.isfinite(pearson_value) else 0.0
            spearman_abs = abs(spearman_value) if np.isfinite(spearman_value) else 0.0

            if max(pearson_abs, spearman_abs) < threshold:
                continue

            pairs.append(
                CorrelationPair(
                    column_a=col_a,
                    column_b=col_b,
                    pearson=float(pearson_value) if np.isfinite(pearson_value) else None,
                    spearman=float(spearman_value) if np.isfinite(spearman_value) else None,
                )
            )

    pairs.sort(
        key=lambda pair: max(
            abs(pair.pearson or 0.0),
            abs(pair.spearman or 0.0),
        ),
        reverse=True,
    )
    return pairs


def compute_correlation_report(
    df: pd.DataFrame,
    logical_types: dict[str, LogicalType],
) -> CorrelationReport:
    """
    Compute Pearson and Spearman correlation matrices across numeric dimensions.

    Returns a report with matrices, strong dependency pairs, and any warnings.
    """
    warnings: list[str] = []
    numeric_columns = _select_numeric_columns(df, logical_types)

    if len(numeric_columns) < MIN_NUMERIC_COLUMNS:
        warnings.append(
            f"Correlation analysis requires at least {MIN_NUMERIC_COLUMNS} numeric "
            f"columns; found {len(numeric_columns)}."
        )
        return CorrelationReport(
            numeric_columns=numeric_columns,
            pearson_matrix=[],
            spearman_matrix=[],
            strong_pairs=[],
            warnings=warnings,
        )

    numeric_df = _build_numeric_frame(df, numeric_columns)

    constant_columns = [
        column
        for column in numeric_columns
        if numeric_df[column].dropna().std(ddof=1) <= CONSTANT_COLUMN_STD
    ]
    if constant_columns:
        warnings.append(
            "Constant numeric columns excluded from pair analysis: "
            + ", ".join(constant_columns)
        )

    try:
        pearson_matrix = _compute_pearson_matrix(numeric_df)
        spearman_matrix = _compute_spearman_matrix(numeric_df)
    except (ValueError, FloatingPointError) as exc:
        raise CorrelationMatrixError(f"Failed to compute correlation matrices: {exc}") from exc

    strong_pairs = _extract_strong_pairs(pearson_matrix, spearman_matrix)

    return CorrelationReport(
        numeric_columns=numeric_columns,
        pearson_matrix=_matrix_to_json(pearson_matrix),
        spearman_matrix=_matrix_to_json(spearman_matrix),
        strong_pairs=strong_pairs,
        warnings=warnings,
    )
