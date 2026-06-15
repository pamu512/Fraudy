from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.engines.common import coerce_numeric_series, resolve_column_name
from app.engines.types import EngineResult, RuleHit

# Expected Benford first-digit frequencies: P(d) = log10(1 + 1/d).
BENFORD_EXPECTED: dict[int, float] = {
    digit: math.log10(1 + 1 / digit) for digit in range(1, 10)
}

# Chi-square critical value (df=8, alpha=0.05).
CHI_SQUARE_CRITICAL = 15.507
MIN_SAMPLE_SIZE = 10


def _first_digit(value: float) -> int | None:
    if not np.isfinite(value) or value == 0:
        return None

    magnitude = abs(value)
    while magnitude >= 10:
        magnitude /= 10
    while magnitude < 1:
        magnitude *= 10

    digit = int(magnitude)
    return digit if 1 <= digit <= 9 else None


def _extract_first_digits(values: pd.Series) -> tuple[dict[int, float], list[int]]:
    digits: list[int] = []
    source_indices: list[int] = []

    for idx, raw in values.items():
        digit = _first_digit(float(raw))
        if digit is not None:
            digits.append(digit)
            source_indices.append(int(idx))

    if not digits:
        return {}, []

    counts = pd.Series(digits).value_counts(normalize=True)
    observed = {digit: float(counts.get(digit, 0.0)) for digit in range(1, 10)}
    return observed, source_indices


def _chi_square_statistic(observed: dict[int, float], sample_size: int) -> float:
    if sample_size == 0:
        return 0.0

    statistic = 0.0
    for digit in range(1, 10):
        expected = BENFORD_EXPECTED[digit] * sample_size
        observed_count = observed.get(digit, 0.0) * sample_size
        if expected > 0:
            statistic += (observed_count - expected) ** 2 / expected
    return statistic


def _mad_score(observed: dict[int, float]) -> float:
    """Mean absolute deviation from Benford expectations, normalized 0-1."""
    deviations = [
        abs(observed.get(digit, 0.0) - BENFORD_EXPECTED[digit])
        for digit in range(1, 10)
    ]
    mad = sum(deviations) / len(deviations)
    return min(mad / 0.05, 1.0)


def _analyze_column(column: str, series: pd.Series) -> RuleHit:
    positive = series[series != 0]
    if len(positive) < MIN_SAMPLE_SIZE:
        return RuleHit(
            rule_name=f"benford_{column}",
            passed=True,
            score=0.0,
            reason=(
                f"Column '{column}' has fewer than {MIN_SAMPLE_SIZE} non-zero "
                "numeric values; Benford chi-square test skipped."
            ),
            affected_columns=[column],
        )

    observed, row_indices = _extract_first_digits(positive)
    sample_size = len(row_indices)
    chi_square = _chi_square_statistic(observed, sample_size)
    mad_normalized = _mad_score(observed)
    passed = chi_square <= CHI_SQUARE_CRITICAL
    score = min(chi_square / CHI_SQUARE_CRITICAL, 1.0)

    worst_digit = max(
        range(1, 10),
        key=lambda digit: abs(observed.get(digit, 0.0) - BENFORD_EXPECTED[digit]),
    )
    expected_pct = BENFORD_EXPECTED[worst_digit] * 100
    observed_pct = observed.get(worst_digit, 0.0) * 100

    details: dict[str, Any] = {
        "sample_size": sample_size,
        "chi_square": round(chi_square, 4),
        "chi_square_critical": CHI_SQUARE_CRITICAL,
        "chi_square_divergence": round(max(chi_square - CHI_SQUARE_CRITICAL, 0.0), 4),
        "mad_score": round(mad_normalized, 4),
        "expected_distribution": {
            str(digit): round(BENFORD_EXPECTED[digit], 4) for digit in range(1, 10)
        },
        "observed_distribution": {
            str(digit): round(observed.get(digit, 0.0), 4) for digit in range(1, 10)
        },
        "largest_deviation_digit": worst_digit,
    }

    if passed:
        reason = (
            f"Column '{column}' leading-digit distribution aligns with Benford's Law "
            f"(chi-square={chi_square:.2f}, critical={CHI_SQUARE_CRITICAL}). "
            "No evidence of manual digit manipulation."
        )
        final_score = 0.0
    else:
        reason = (
            f"Column '{column}' deviates from Benford's Law "
            f"(chi-square={chi_square:.2f} > {CHI_SQUARE_CRITICAL}). "
            f"Digit {worst_digit} observed at {observed_pct:.1f}% vs expected "
            f"{expected_pct:.1f}%, suggesting possible manual manipulation."
        )
        final_score = round(max(score, mad_normalized), 4)

    return RuleHit(
        rule_name=f"benford_{column}",
        passed=passed,
        score=final_score,
        reason=reason,
        affected_columns=[column],
        affected_rows=[] if passed else row_indices[:50],
        details=details,
    )


def _select_numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in df.columns
        if coerce_numeric_series(df[column]).notna().sum() >= MIN_SAMPLE_SIZE
    ]


def run_benfords_law(
    df: pd.DataFrame,
    column: str | None = None,
) -> EngineResult:
    """
    Test leading-digit distribution against Benford's Law using chi-square divergence.

    When ``column`` is provided, only that designated numerical column is tested.
    Otherwise all numeric columns with sufficient data are evaluated.
    """
    engine = "benfords_law"

    if df.empty or df.shape[1] == 0:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="benford_input_validation",
                    passed=False,
                    score=0.0,
                    reason="No columns available for Benford analysis.",
                )
            ],
        )

    if column is not None:
        resolved = resolve_column_name(df, column)
        if resolved is None:
            return EngineResult(
                engine=engine,
                hits=[
                    RuleHit(
                        rule_name="benford_column_not_found",
                        passed=False,
                        score=0.0,
                        reason=f"Designated column '{column}' was not found in the dataset.",
                    )
                ],
            )

        numeric = coerce_numeric_series(df[resolved]).dropna()
        if len(numeric) < MIN_SAMPLE_SIZE:
            return EngineResult(
                engine=engine,
                hits=[
                    RuleHit(
                        rule_name=f"benford_{resolved}",
                        passed=False,
                        score=0.0,
                        reason=(
                            f"Designated column '{resolved}' has fewer than "
                            f"{MIN_SAMPLE_SIZE} numeric values for Benford analysis."
                        ),
                        affected_columns=[resolved],
                    )
                ],
            )

        return EngineResult(
            engine=engine,
            hits=[_analyze_column(resolved, numeric)],
        )

    numeric_columns = _select_numeric_columns(df)
    if not numeric_columns:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="benford_numeric_columns",
                    passed=True,
                    score=0.0,
                    reason=(
                        f"No numeric columns with at least {MIN_SAMPLE_SIZE} valid "
                        "values found; Benford analysis skipped."
                    ),
                )
            ],
        )

    hits = [
        _analyze_column(name, coerce_numeric_series(df[name]).dropna())
        for name in numeric_columns
    ]
    return EngineResult(engine=engine, hits=hits)
