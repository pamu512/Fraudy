from __future__ import annotations

import pandas as pd


def coerce_numeric_series(series: pd.Series) -> pd.Series:
    """Parse spreadsheet-style numeric strings into floats."""
    cleaned = (
        series.astype(str)
        .str.replace(r"[\$,]", "", regex=True)
        .str.replace(r"\(([^)]+)\)", r"-\1", regex=True)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def resolve_column_name(df: pd.DataFrame, column: str) -> str | None:
    """Resolve a column name case-insensitively against the dataframe."""
    if column in df.columns:
        return column
    normalized = column.strip().lower()
    for name in df.columns:
        if str(name).strip().lower() == normalized:
            return str(name)
    return None
