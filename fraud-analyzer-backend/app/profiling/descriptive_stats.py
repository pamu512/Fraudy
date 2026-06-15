from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from app.profiling.types import LogicalType

MIN_SAMPLE_SIZE = 2


class DescriptiveStatsError(ValueError):
    """Raised when descriptive statistics cannot be computed."""


def _safe_round(value: float | int | None, digits: int = 4) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _numeric_stats(series: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < MIN_SAMPLE_SIZE:
        raise DescriptiveStatsError(
            f"Need at least {MIN_SAMPLE_SIZE} numeric values; got {len(values)}."
        )

    q25, q50, q75 = np.percentile(values, [25, 50, 75])
    skew = stats.skew(values, bias=False) if len(values) >= 3 else None
    kurtosis = stats.kurtosis(values, bias=False) if len(values) >= 4 else None

    return {
        "count": int(len(values)),
        "mean": _safe_round(values.mean()),
        "std": _safe_round(values.std(ddof=1)) if len(values) > 1 else 0.0,
        "min": _safe_round(values.min()),
        "q25": _safe_round(q25),
        "median": _safe_round(q50),
        "q75": _safe_round(q75),
        "max": _safe_round(values.max()),
        "range": _safe_round(values.max() - values.min()),
        "skewness": _safe_round(skew),
        "kurtosis": _safe_round(kurtosis),
        "zeros": int((values == 0).sum()),
        "negatives": int((values < 0).sum()),
    }


def _datetime_stats(series: pd.Series) -> dict[str, Any]:
    values = pd.to_datetime(series, errors="coerce").dropna()
    if values.empty:
        raise DescriptiveStatsError("No parseable datetime values.")

    min_ts = values.min()
    max_ts = values.max()
    span = max_ts - min_ts

    return {
        "count": int(len(values)),
        "min": min_ts.isoformat(),
        "max": max_ts.isoformat(),
        "range_days": span.days,
        "range_seconds": int(span.total_seconds()),
    }


def _categorical_stats(series: pd.Series) -> dict[str, Any]:
    values = series.dropna().astype(str)
    if values.empty:
        raise DescriptiveStatsError("No categorical values.")

    counts = values.value_counts()
    top_value = counts.index[0]
    top_count = int(counts.iloc[0])

    return {
        "count": int(len(values)),
        "unique": int(len(counts)),
        "top_value": str(top_value),
        "top_frequency": top_count,
        "top_ratio": _safe_round(top_count / len(values)),
        "entropy": _safe_round(stats.entropy(counts.values)),
    }


def _boolean_stats(series: pd.Series) -> dict[str, Any]:
    values = series.dropna()
    if values.empty:
        raise DescriptiveStatsError("No boolean values.")

    true_count = int(values.astype(bool).sum())
    false_count = int(len(values) - true_count)

    return {
        "count": int(len(values)),
        "true_count": true_count,
        "false_count": false_count,
        "true_ratio": _safe_round(true_count / len(values)),
    }


def _string_stats(series: pd.Series) -> dict[str, Any]:
    values = series.dropna().astype(str)
    if values.empty:
        raise DescriptiveStatsError("No string values.")

    lengths = values.str.len()
    return {
        "count": int(len(values)),
        "unique": int(values.nunique()),
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
        "mean_length": _safe_round(lengths.mean()),
    }


def compute_descriptive_stats(
    logical_type: LogicalType,
    series: pd.Series,
) -> dict[str, Any]:
    """
    Compute descriptive statistics appropriate to the inferred logical type.

    Returns an empty dict when insufficient data prevents computation.
    """
    handlers = {
        LogicalType.INTEGER: _numeric_stats,
        LogicalType.FLOAT: _numeric_stats,
        LogicalType.DATETIME: _datetime_stats,
        LogicalType.CATEGORICAL: _categorical_stats,
        LogicalType.BOOLEAN: _boolean_stats,
        LogicalType.STRING: _string_stats,
    }

    handler = handlers.get(logical_type)
    if handler is None:
        return {"note": "No descriptive statistics available for unknown type."}

    try:
        return handler(series)
    except DescriptiveStatsError as exc:
        return {"note": str(exc)}
