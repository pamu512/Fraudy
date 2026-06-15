from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LogicalType(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    STRING = "string"
    CATEGORICAL = "categorical"
    UNKNOWN = "unknown"


class IdentifierRole(StrEnum):
    TRANSACTION_ID = "transaction_id"
    TIMESTAMP = "timestamp"
    AMOUNT = "amount"
    ENTITY_ID = "entity_id"
    CATEGORY = "category"


@dataclass
class IdentifierFlag:
    role: IdentifierRole
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


@dataclass
class ColumnProfile:
    name: str
    logical_type: LogicalType
    pandas_dtype: str
    null_count: int
    null_pct: float
    unique_count: int
    sample_values: list[Any]
    identifier_flags: list[IdentifierFlag] = field(default_factory=list)
    descriptive_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "logical_type": self.logical_type.value,
            "pandas_dtype": self.pandas_dtype,
            "null_count": self.null_count,
            "null_pct": round(self.null_pct, 4),
            "unique_count": self.unique_count,
            "sample_values": self.sample_values,
            "identifier_flags": [flag.to_dict() for flag in self.identifier_flags],
            "descriptive_stats": self.descriptive_stats,
        }


@dataclass
class CorrelationPair:
    column_a: str
    column_b: str
    pearson: float | None
    spearman: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_a": self.column_a,
            "column_b": self.column_b,
            "pearson": None if self.pearson is None else round(self.pearson, 4),
            "spearman": None if self.spearman is None else round(self.spearman, 4),
        }


@dataclass
class CorrelationReport:
    numeric_columns: list[str]
    pearson_matrix: list[list[float | None]]
    spearman_matrix: list[list[float | None]]
    strong_pairs: list[CorrelationPair] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "numeric_columns": self.numeric_columns,
            "pearson_matrix": self.pearson_matrix,
            "spearman_matrix": self.spearman_matrix,
            "strong_pairs": [pair.to_dict() for pair in self.strong_pairs],
            "warnings": self.warnings,
        }


@dataclass
class DatasetProfile:
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    correlations: CorrelationReport
    source_filename: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": {
                "row_count": self.row_count,
                "column_count": self.column_count,
                "source_filename": self.source_filename,
            },
            "columns": [column.to_dict() for column in self.columns],
            "correlations": self.correlations.to_dict(),
            "warnings": self.warnings,
        }
