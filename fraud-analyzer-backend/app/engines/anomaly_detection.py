from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from app.engines.common import coerce_numeric_series, resolve_column_name
from app.engines.types import EngineResult, RuleHit

AnomalyMethod = Literal["isolation_forest", "rolling_zscore"]

MIN_ROWS = 5
MIN_NUMERIC_COLUMNS = 1
CONTAMINATION = 0.1
RANDOM_STATE = 42
DEFAULT_ZSCORE_THRESHOLD = 3.0
DEFAULT_WINDOW_SIZE = 20


def _coerce_numeric_frame(
    df: pd.DataFrame,
    columns: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    target_columns = columns if columns else [str(col) for col in df.columns]
    numeric_cols: list[str] = []
    frames: dict[str, pd.Series] = {}

    for raw_name in target_columns:
        resolved = resolve_column_name(df, raw_name)
        if resolved is None:
            continue
        series = coerce_numeric_series(df[resolved])
        if series.notna().sum() >= MIN_ROWS:
            numeric_cols.append(resolved)
            frames[resolved] = series

    if not numeric_cols:
        return pd.DataFrame(), []

    return pd.DataFrame(frames), numeric_cols


def _normalize_anomaly_score(raw_score: float, min_score: float, max_score: float) -> float:
    score_range = max_score - min_score if max_score != min_score else 1.0
    return round(1.0 - ((raw_score - min_score) / score_range), 4)


def _feature_contributions(
    row: pd.Series,
    feature_means: pd.Series,
    feature_stds: pd.Series,
) -> dict[str, float]:
    contributions: dict[str, float] = {}
    for col in row.index:
        std = feature_stds[col]
        if std == 0 or not np.isfinite(std):
            contributions[col] = 0.0
            continue
        contributions[col] = float(abs((row[col] - feature_means[col]) / std))
    return contributions


def _build_row_hit(
    row_index: int,
    anomaly_score: float,
    reason: str,
    method: str,
    deviating_features: list[tuple[str, float]],
    details: dict[str, Any],
) -> RuleHit:
    return RuleHit(
        rule_name=f"anomaly_row_{row_index}",
        passed=False,
        score=round(anomaly_score, 4),
        reason=reason,
        affected_columns=[name for name, _ in deviating_features],
        affected_rows=[row_index],
        details={
            "anomaly_score": round(anomaly_score, 4),
            "method": method,
            **details,
        },
    )


def _run_isolation_forest(
    feature_matrix: pd.DataFrame,
    numeric_cols: list[str],
) -> list[RuleHit]:
    hits: list[RuleHit] = []

    scaler = StandardScaler()
    scaled = scaler.fit_transform(feature_matrix)

    contamination = min(CONTAMINATION, max(1 / len(feature_matrix), 0.01))
    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=RANDOM_STATE,
    )
    predictions = model.fit_predict(scaled)
    decision_scores = model.decision_function(scaled)
    row_index_map = list(feature_matrix.index)

    feature_means = feature_matrix.mean()
    feature_stds = feature_matrix.std(ddof=0).replace(0, np.nan).fillna(1.0)

    anomaly_indices = [
        int(row_index_map[i]) for i, pred in enumerate(predictions) if pred == -1
    ]

    if not anomaly_indices:
        hits.append(
            RuleHit(
                rule_name="isolation_forest_scan",
                passed=True,
                score=0.0,
                reason=(
                    f"Isolation Forest scanned {len(feature_matrix)} rows across "
                    f"{len(numeric_cols)} numeric columns; no anomalies flagged."
                ),
                affected_columns=numeric_cols,
                details={
                    "method": "isolation_forest",
                    "rows_scanned": len(feature_matrix),
                    "columns_used": numeric_cols,
                    "contamination": round(contamination, 4),
                },
            )
        )
        return hits

    min_score = float(decision_scores.min())
    max_score = float(decision_scores.max())

    for idx in anomaly_indices[:50]:
        position = row_index_map.index(idx)
        raw_score = float(decision_scores[position])
        anomaly_score = _normalize_anomaly_score(raw_score, min_score, max_score)
        contributions = _feature_contributions(
            feature_matrix.loc[idx],
            feature_means,
            feature_stds,
        )
        top_features = sorted(
            contributions.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        top_summary = ", ".join(
            f"{name} (z={z:.2f})" for name, z in top_features if z > 0
        )

        hits.append(
            _build_row_hit(
                row_index=idx,
                anomaly_score=anomaly_score,
                reason=(
                    f"Row {idx} flagged as an anomaly by Isolation Forest "
                    f"(score={anomaly_score:.2f}). "
                    f"Deviating features: {top_summary or 'none identified'}."
                ),
                method="isolation_forest",
                deviating_features=top_features,
                details={
                    "decision_score": round(raw_score, 4),
            "z_score_contributions": {
                key: round(value, 4) for key, value in contributions.items()
            },
                    "top_deviating_features": [
                        {"column": name, "z_score": round(z, 4)}
                        for name, z in top_features
                    ],
                },
            )
        )

    summary_score = min(len(anomaly_indices) / len(feature_matrix), 1.0)
    hits.insert(
        0,
        RuleHit(
            rule_name="isolation_forest_summary",
            passed=False,
            score=round(summary_score, 4),
            reason=(
                f"Isolation Forest flagged {len(anomaly_indices)} of "
                f"{len(feature_matrix)} rows as anomalous "
                f"({summary_score * 100:.1f}% of scanned data)."
            ),
            affected_columns=numeric_cols,
            affected_rows=anomaly_indices[:50],
            details={
                "method": "isolation_forest",
                "anomaly_count": len(anomaly_indices),
                "rows_scanned": len(feature_matrix),
                "columns_used": numeric_cols,
            },
        ),
    )
    return hits


def _run_rolling_zscore(
    feature_matrix: pd.DataFrame,
    numeric_cols: list[str],
    window_size: int,
    zscore_threshold: float,
) -> list[RuleHit]:
    hits: list[RuleHit] = []
    min_periods = max(3, window_size // 2)
    row_scores: dict[int, list[tuple[str, float, float]]] = {}

    for column in numeric_cols:
        series = feature_matrix[column]
        rolling_mean = series.rolling(window=window_size, min_periods=min_periods).mean()
        rolling_std = series.rolling(window=window_size, min_periods=min_periods).std(ddof=0)
        z_scores = (series - rolling_mean) / rolling_std.replace(0, np.nan)

        for idx, z_value in z_scores.items():
            if not np.isfinite(z_value) or abs(z_value) < zscore_threshold:
                continue
            row_index = int(idx)
            row_scores.setdefault(row_index, []).append(
                (column, float(z_value), float(series.loc[idx]))
            )

    if not row_scores:
        hits.append(
            RuleHit(
                rule_name="rolling_zscore_scan",
                passed=True,
                score=0.0,
                reason=(
                    f"Rolling Z-score scan across {len(feature_matrix)} rows found "
                    f"no values exceeding threshold {zscore_threshold}."
                ),
                affected_columns=numeric_cols,
                details={
                    "method": "rolling_zscore",
                    "window_size": window_size,
                    "zscore_threshold": zscore_threshold,
                    "rows_scanned": len(feature_matrix),
                    "columns_used": numeric_cols,
                },
            )
        )
        return hits

    for row_index in sorted(row_scores.keys())[:50]:
        deviations = row_scores[row_index]
        max_z = max(abs(z) for _, z, _ in deviations)
        anomaly_score = round(min(max_z / (zscore_threshold * 2), 1.0), 4)
        feature_summary = ", ".join(
            f"{column} (z={z:+.2f}, value={value:.4g})"
            for column, z, value in sorted(
                deviations,
                key=lambda item: abs(item[1]),
                reverse=True,
            )[:3]
        )

        hits.append(
            _build_row_hit(
                row_index=row_index,
                anomaly_score=anomaly_score,
                reason=(
                    f"Row {row_index} exceeds rolling Z-score threshold "
                    f"({zscore_threshold}) on: {feature_summary}."
                ),
                method="rolling_zscore",
                deviating_features=[
                    (column, abs(z)) for column, z, _ in deviations
                ],
                details={
                    "window_size": window_size,
                    "zscore_threshold": zscore_threshold,
                    "deviations": [
                        {
                            "column": column,
                            "z_score": round(z, 4),
                            "value": round(value, 4),
                        }
                        for column, z, value in deviations
                    ],
                },
            )
        )

    summary_score = min(len(row_scores) / len(feature_matrix), 1.0)
    hits.insert(
        0,
        RuleHit(
            rule_name="rolling_zscore_summary",
            passed=False,
            score=round(summary_score, 4),
            reason=(
                f"Rolling Z-score flagged {len(row_scores)} of "
                f"{len(feature_matrix)} rows (window={window_size}, "
                f"threshold={zscore_threshold})."
            ),
            affected_columns=numeric_cols,
            affected_rows=sorted(row_scores.keys())[:50],
            details={
                "method": "rolling_zscore",
                "anomaly_count": len(row_scores),
                "window_size": window_size,
                "zscore_threshold": zscore_threshold,
                "rows_scanned": len(feature_matrix),
                "columns_used": numeric_cols,
            },
        ),
    )
    return hits


def run_anomaly_detection(
    df: pd.DataFrame,
    method: AnomalyMethod = "isolation_forest",
    columns: list[str] | None = None,
    zscore_threshold: float = DEFAULT_ZSCORE_THRESHOLD,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> EngineResult:
    """
    Detect transaction anomalies using Isolation Forest or rolling Z-score.

    Returns per-row anomaly scores and explainable reasons for each flagged row.
    """
    engine = "anomaly_detection"

    if df.empty:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="anomaly_input_validation",
                    passed=False,
                    score=0.0,
                    reason="No rows available for anomaly detection.",
                )
            ],
        )

    if method not in {"isolation_forest", "rolling_zscore"}:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="anomaly_method_validation",
                    passed=False,
                    score=0.0,
                    reason=(
                        f"Unsupported anomaly method '{method}'. "
                        "Use 'isolation_forest' or 'rolling_zscore'."
                    ),
                )
            ],
        )

    if zscore_threshold <= 0:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="anomaly_threshold_validation",
                    passed=False,
                    score=0.0,
                    reason="zscore_threshold must be greater than zero.",
                )
            ],
        )

    if window_size < 3:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="anomaly_window_validation",
                    passed=False,
                    score=0.0,
                    reason="window_size must be at least 3 for rolling Z-score.",
                )
            ],
        )

    numeric_df, numeric_cols = _coerce_numeric_frame(df, columns)

    if columns:
        missing = [
            name
            for name in columns
            if resolve_column_name(df, name) is None
        ]
        if missing:
            return EngineResult(
                engine=engine,
                hits=[
                    RuleHit(
                        rule_name="anomaly_columns_not_found",
                        passed=False,
                        score=0.0,
                        reason=(
                            "Designated columns not found: "
                            + ", ".join(missing)
                        ),
                    )
                ],
            )

    if len(numeric_cols) < MIN_NUMERIC_COLUMNS:
        return EngineResult(
            engine=engine,
            hits=[
                RuleHit(
                    rule_name="anomaly_numeric_columns",
                    passed=True,
                    score=0.0,
                    reason=(
                        f"Insufficient numeric columns with at least {MIN_ROWS} "
                        "values; anomaly detection skipped."
                    ),
                )
            ],
        )

    complete_rows = numeric_df.dropna()
    if len(complete_rows) >= MIN_ROWS:
        feature_matrix = complete_rows
    else:
        feature_matrix = numeric_df.fillna(numeric_df.median(numeric_only=True))
        if len(feature_matrix) < MIN_ROWS:
            return EngineResult(
                engine=engine,
                hits=[
                    RuleHit(
                        rule_name="anomaly_row_count",
                        passed=True,
                        score=0.0,
                        reason=(
                            f"Fewer than {MIN_ROWS} rows with numeric data; "
                            "anomaly detection skipped."
                        ),
                    )
                ],
            )

    try:
        if method == "isolation_forest":
            hits = _run_isolation_forest(feature_matrix, numeric_cols)
        else:
            hits = _run_rolling_zscore(
                feature_matrix,
                numeric_cols,
                window_size=window_size,
                zscore_threshold=zscore_threshold,
            )
    except ValueError as exc:
        return EngineResult(engine=engine, hits=[], error=str(exc))

    return EngineResult(engine=engine, hits=hits)
