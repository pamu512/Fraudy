from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.engines.types import EngineResult, RuleHit

MIN_SAMPLE = 5
LOW_VARIANCE_CV_THRESHOLD = 0.01
HIGH_VARIANCE_CV_THRESHOLD = 2.0


def _coerce_numeric_series(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(r"[\$,]", "", regex=True)
        .str.replace(r"\(([^)]+)\)", r"-\1", regex=True)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


CHI_SQUARE_CRITICAL_BY_DF: dict[int, float] = {
    1: 3.841,
    2: 5.991,
    3: 7.815,
    4: 9.488,
    5: 11.070,
    6: 12.592,
    7: 14.067,
    8: 15.507,
    9: 16.919,
    10: 18.307,
}


def _chi_square_uniformity_test(
    observed: np.ndarray,
    expected: np.ndarray,
) -> tuple[float, float, bool]:
    observed = observed.astype(float)
    expected = expected.astype(float)
    mask = expected > 0
    if not np.any(mask):
        return 0.0, 0.0, True

    statistic = float(np.sum((observed[mask] - expected[mask]) ** 2 / expected[mask]))
    df = int(np.count_nonzero(mask) - 1)
    critical = CHI_SQUARE_CRITICAL_BY_DF.get(df, 16.919)
    passed = statistic <= critical
    score = min(statistic / critical, 1.0) if critical > 0 else 0.0
    return statistic, score, passed


def _entropy(counts: np.ndarray) -> float:
    total = counts.sum()
    if total <= 0:
        return 0.0
    probabilities = counts[counts > 0] / total
    return float(-np.sum(probabilities * np.log(probabilities)))


def _coefficient_of_variation(values: pd.Series) -> float | None:
    mean = values.mean()
    if mean == 0 or not np.isfinite(mean):
        return None
    std = values.std(ddof=1)
    if not np.isfinite(std):
        return None
    return float(std / abs(mean))


def _analyze_numeric_column(column: str, series: pd.Series) -> list[RuleHit]:
    hits: list[RuleHit] = []
    values = series.dropna()
    n = len(values)

    if n < MIN_SAMPLE:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_sample_size",
                passed=True,
                score=0.0,
                reason=(
                    f"Column '{column}' has fewer than {MIN_SAMPLE} numeric "
                    "values; significance analysis skipped."
                ),
                affected_columns=[column],
            )
        )
        return hits

    cv = _coefficient_of_variation(values)
    unique_ratio = values.nunique() / n
    value_range = float(values.max() - values.min())

    details: dict[str, Any] = {
        "sample_size": n,
        "unique_values": int(values.nunique()),
        "unique_ratio": round(unique_ratio, 4),
        "mean": round(float(values.mean()), 4),
        "std": round(float(values.std(ddof=1)), 4),
        "min": round(float(values.min()), 4),
        "max": round(float(values.max()), 4),
        "range": round(value_range, 4),
    }
    if cv is not None:
        details["coefficient_of_variation"] = round(cv, 4)

    # Low variance: suspicious uniformity (e.g., repeated round amounts).
    if cv is not None and cv < LOW_VARIANCE_CV_THRESHOLD:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_low_variance",
                passed=False,
                score=round(1.0 - cv / LOW_VARIANCE_CV_THRESHOLD, 4),
                reason=(
                    f"Column '{column}' shows unusually low variance "
                    f"(CV={cv:.4f}). Values may be artificially uniform."
                ),
                affected_columns=[column],
                details=details,
            )
        )
    elif cv is not None and cv > HIGH_VARIANCE_CV_THRESHOLD:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_high_variance",
                passed=False,
                score=round(min(cv / HIGH_VARIANCE_CV_THRESHOLD, 1.0), 4),
                reason=(
                    f"Column '{column}' shows unusually high variance "
                    f"(CV={cv:.4f}). Spread is far above typical relative variation."
                ),
                affected_columns=[column],
                details=details,
            )
        )
    else:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_variance",
                passed=True,
                score=0.0,
                reason=(
                    f"Column '{column}' variance appears within normal bounds"
                    + (f" (CV={cv:.4f})." if cv is not None else ".")
                ),
                affected_columns=[column],
                details=details,
            )
        )

    # Chi-square goodness-of-fit against uniform binning (detect artificial patterns).
    if n >= 10 and value_range > 0:
        bin_count = min(10, max(3, int(np.sqrt(n))))
        counts, bin_edges = np.histogram(values, bins=bin_count)
        expected = np.full(bin_count, n / bin_count)
        # Avoid zero-expected bins collapsing the test.
        if np.all(expected > 0) and np.all(counts >= 0):
            chi2, cluster_score, uniform_passed = _chi_square_uniformity_test(
                counts,
                expected,
            )
            uniform_details = {
                **details,
                "uniformity_chi_square": round(float(chi2), 4),
                "uniformity_score": round(cluster_score, 4),
                "bin_count": bin_count,
            }
            if not uniform_passed:
                hits.append(
                    RuleHit(
                        rule_name=f"significance_{column}_non_uniform",
                        passed=False,
                        score=round(cluster_score, 4),
                        reason=(
                            f"Column '{column}' values cluster into non-uniform bins "
                            f"(chi-square={chi2:.2f}). Distribution is not evenly spread."
                        ),
                        affected_columns=[column],
                        details=uniform_details,
                    )
                )
            else:
                hits.append(
                    RuleHit(
                        rule_name=f"significance_{column}_uniformity",
                        passed=True,
                        score=0.0,
                        reason=(
                            f"Column '{column}' does not show significant bin clustering "
                            f"(chi-square={chi2:.2f})."
                        ),
                        affected_columns=[column],
                        details=uniform_details,
                    )
                )

    return hits


def _analyze_categorical_column(column: str, series: pd.Series) -> list[RuleHit]:
    hits: list[RuleHit] = []
    values = series.dropna().astype(str)
    n = len(values)

    if n < MIN_SAMPLE:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_sample_size",
                passed=True,
                score=0.0,
                reason=(
                    f"Column '{column}' has fewer than {MIN_SAMPLE} values; "
                    "significance analysis skipped."
                ),
                affected_columns=[column],
            )
        )
        return hits

    counts = values.value_counts()
    dominant = counts.iloc[0]
    dominant_pct = dominant / n
    entropy = _entropy(counts.values.astype(float))
    max_entropy = np.log(len(counts)) if len(counts) > 1 else 1.0
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0

    details: dict[str, Any] = {
        "sample_size": n,
        "unique_values": int(len(counts)),
        "dominant_value": str(counts.index[0]),
        "dominant_count": int(dominant),
        "dominant_ratio": round(float(dominant_pct), 4),
        "entropy": round(float(entropy), 4),
        "normalized_entropy": round(float(normalized_entropy), 4),
    }

    if dominant_pct > 0.9:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_dominant_value",
                passed=False,
                score=round(float(dominant_pct), 4),
                reason=(
                    f"Column '{column}' is dominated by a single value "
                    f"('{counts.index[0]}' at {dominant_pct * 100:.1f}%). "
                    "Low diversity may indicate data quality or manipulation issues."
                ),
                affected_columns=[column],
                details=details,
            )
        )
    else:
        hits.append(
            RuleHit(
                rule_name=f"significance_{column}_diversity",
                passed=True,
                score=0.0,
                reason=(
                    f"Column '{column}' shows acceptable value diversity "
                    f"(dominant value {dominant_pct * 100:.1f}%, "
                    f"entropy={normalized_entropy:.2f})."
                ),
                affected_columns=[column],
                details=details,
            )
        )

    return hits


def run_column_significance(df: pd.DataFrame) -> EngineResult:
    engine = "column_significance"
    hits: list[RuleHit] = []

    if df.empty or df.shape[1] == 0:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="significance_input_validation",
                    passed=False,
                    score=0.0,
                    reason="No columns available for significance analysis.",
                )
            ],
        )

    for column in df.columns:
        numeric = _coerce_numeric_series(df[column])
        numeric_count = numeric.notna().sum()
        non_null = df[column].notna().sum()

        if numeric_count >= MIN_SAMPLE and numeric_count >= non_null * 0.8:
            hits.extend(_analyze_numeric_column(column, numeric))
        elif non_null >= MIN_SAMPLE:
            hits.extend(_analyze_categorical_column(column, df[column]))
        else:
            hits.append(
                RuleHit(
                    rule_name=f"significance_{column}_insufficient_data",
                    passed=True,
                    score=0.0,
                    reason=(
                        f"Column '{column}' has insufficient data "
                        f"({non_null} non-null values) for analysis."
                    ),
                    affected_columns=[column],
                )
            )

    return EngineResult(engine=engine, hits=hits)
