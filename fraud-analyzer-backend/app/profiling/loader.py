from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".json", ".xlsx", ".xls", ".parquet"}
CSV_EXTENSIONS = {".csv", ".tsv", ".txt"}


class DatasetLoadError(ValueError):
    """Raised when an uploaded dataset cannot be parsed."""


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def _read_csv(content: bytes, filename: str) -> pd.DataFrame:
    sep = "\t" if _extension(filename) == ".tsv" else ","
    try:
        return pd.read_csv(io.BytesIO(content), sep=sep)
    except pd.errors.EmptyDataError as exc:
        raise DatasetLoadError("Uploaded file contains no data.") from exc
    except pd.errors.ParserError as exc:
        raise DatasetLoadError(f"Failed to parse CSV/TSV file: {exc}") from exc


def _read_json(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_json(io.BytesIO(content))
    except ValueError as exc:
        raise DatasetLoadError(f"Failed to parse JSON file: {exc}") from exc


def _read_excel(content: bytes, filename: str) -> pd.DataFrame:
    engine = "openpyxl" if _extension(filename) == ".xlsx" else None
    try:
        return pd.read_excel(io.BytesIO(content), engine=engine)
    except ImportError as exc:
        raise DatasetLoadError(
            "Excel support requires openpyxl (xlsx) or xlrd (xls). "
            "Install openpyxl for .xlsx files."
        ) from exc
    except ValueError as exc:
        raise DatasetLoadError(f"Failed to parse Excel file: {exc}") from exc


def _read_parquet(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_parquet(io.BytesIO(content))
    except ImportError as exc:
        raise DatasetLoadError(
            "Parquet support requires pyarrow. Install pyarrow to read .parquet files."
        ) from exc
    except Exception as exc:
        raise DatasetLoadError(f"Failed to parse Parquet file: {exc}") from exc


def load_dataset(content: bytes, filename: str) -> pd.DataFrame:
    """
    Load a raw uploaded dataset into a pandas DataFrame.

    Raises:
        DatasetLoadError: For unsupported formats, empty files, or parse failures.
    """
    if not content:
        raise DatasetLoadError("Uploaded file is empty.")

    if not filename or not filename.strip():
        raise DatasetLoadError("Filename is required to detect file format.")

    ext = _extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DatasetLoadError(
            f"Unsupported file type '{ext or '(none)'}'. Supported: {supported}."
        )

    if ext in CSV_EXTENSIONS:
        df = _read_csv(content, filename)
    elif ext == ".json":
        df = _read_json(content)
    elif ext in {".xlsx", ".xls"}:
        df = _read_excel(content, filename)
    elif ext == ".parquet":
        df = _read_parquet(content)
    else:
        raise DatasetLoadError(f"Unsupported file type: {ext}")

    if df.empty:
        raise DatasetLoadError("Dataset contains no rows.")

    if df.shape[1] == 0:
        raise DatasetLoadError("Dataset contains no columns.")

    df.columns = [str(column).strip() for column in df.columns]
    return df
