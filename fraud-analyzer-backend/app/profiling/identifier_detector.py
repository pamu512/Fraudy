from __future__ import annotations

import re

import pandas as pd

from app.profiling.schema_mapper import _normalize_column_name
from app.profiling.types import IdentifierFlag, IdentifierRole, LogicalType

UNIQUENESS_ID_THRESHOLD = 0.95
UNIQUENESS_ENTITY_THRESHOLD = 0.80
AMOUNT_NAME_PATTERN = re.compile(
    r"(amount|price|value|total|cost|debit|credit|balance|fee|payment|amt|sum|subtotal|tax)",
    re.IGNORECASE,
)
TIMESTAMP_NAME_PATTERN = re.compile(
    r"(date|time|timestamp|created|updated|posted|occurred|transaction_date|txn_date|datetime)",
    re.IGNORECASE,
)
TRANSACTION_ID_NAME_PATTERN = re.compile(
    r"(transaction.?id|txn.?id|trans.?id|reference|ref.?no|invoice.?id|order.?id|payment.?id|id$|^id$|uuid|guid)",
    re.IGNORECASE,
)
ENTITY_ID_NAME_PATTERN = re.compile(
    r"(customer.?id|user.?id|account.?id|merchant.?id|vendor.?id|client.?id|entity.?id|card.?id)",
    re.IGNORECASE,
)
CATEGORY_NAME_PATTERN = re.compile(
    r"(category|type|status|channel|method|class|segment|department|region|country|currency)",
    re.IGNORECASE,
)


def _name_match(pattern: re.Pattern[str], column_name: str) -> bool:
    normalized = _normalize_column_name(column_name)
    return bool(pattern.search(normalized))


def _uniqueness_ratio(series: pd.Series) -> float:
    non_null = series.dropna()
    if non_null.empty:
        return 0.0
    return float(non_null.nunique() / len(non_null))


def _score_transaction_id(
    column_name: str,
    logical_type: LogicalType,
    series: pd.Series,
) -> IdentifierFlag | None:
    uniqueness = _uniqueness_ratio(series)
    name_hit = _name_match(TRANSACTION_ID_NAME_PATTERN, column_name)
    type_ok = logical_type in {LogicalType.INTEGER, LogicalType.STRING, LogicalType.CATEGORICAL}

    if not type_ok:
        return None

    confidence = 0.0
    reasons: list[str] = []

    if name_hit:
        confidence += 0.45
        reasons.append("column name matches transaction/reference ID patterns")
    if uniqueness >= UNIQUENESS_ID_THRESHOLD:
        confidence += 0.45
        reasons.append(f"high uniqueness ratio ({uniqueness:.2f})")
    elif uniqueness >= 0.70 and name_hit:
        confidence += 0.25
        reasons.append(f"moderate uniqueness ({uniqueness:.2f}) with ID-like name")

    if confidence < 0.40:
        return None

    return IdentifierFlag(
        role=IdentifierRole.TRANSACTION_ID,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons),
    )


def _score_timestamp(
    column_name: str,
    logical_type: LogicalType,
    series: pd.Series,
) -> IdentifierFlag | None:
    name_hit = _name_match(TIMESTAMP_NAME_PATTERN, column_name)
    confidence = 0.0
    reasons: list[str] = []

    if logical_type == LogicalType.DATETIME:
        confidence += 0.60
        reasons.append("values parse as datetime")
    if name_hit:
        confidence += 0.35
        reasons.append("column name matches date/time patterns")

    if confidence < 0.40:
        return None

    non_null = series.dropna()
    if not non_null.empty and logical_type == LogicalType.DATETIME:
        span = non_null.max() - non_null.min()
        if span.total_seconds() > 0:
            reasons.append(f"temporal span covers {span.days} day(s)")

    return IdentifierFlag(
        role=IdentifierRole.TIMESTAMP,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons),
    )


def _score_amount(
    column_name: str,
    logical_type: LogicalType,
    series: pd.Series,
) -> IdentifierFlag | None:
    if logical_type not in {LogicalType.INTEGER, LogicalType.FLOAT}:
        return None

    name_hit = _name_match(AMOUNT_NAME_PATTERN, column_name)
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None

    has_decimals = logical_type == LogicalType.FLOAT or (numeric % 1 != 0).any()
    has_negative = (numeric < 0).any()
    value_range = float(numeric.max() - numeric.min())

    confidence = 0.0
    reasons: list[str] = []

    if name_hit:
        confidence += 0.50
        reasons.append("column name matches monetary amount patterns")
    if has_decimals:
        confidence += 0.15
        reasons.append("contains fractional values typical of currency")
    if has_negative:
        confidence += 0.10
        reasons.append("contains negative values (debits/refunds)")
    if value_range > 0:
        confidence += 0.15
        reasons.append(f"non-zero numeric spread (range={value_range:.2f})")

    if confidence < 0.40:
        return None

    return IdentifierFlag(
        role=IdentifierRole.AMOUNT,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons),
    )


def _score_entity_id(
    column_name: str,
    logical_type: LogicalType,
    series: pd.Series,
) -> IdentifierFlag | None:
    if logical_type not in {LogicalType.INTEGER, LogicalType.STRING, LogicalType.CATEGORICAL}:
        return None

    name_hit = _name_match(ENTITY_ID_NAME_PATTERN, column_name)
    uniqueness = _uniqueness_ratio(series)

    if not name_hit:
        return None

    confidence = 0.50
    reasons = ["column name matches entity ID patterns"]

    if uniqueness >= UNIQUENESS_ENTITY_THRESHOLD:
        confidence += 0.30
        reasons.append(f"high cardinality ({uniqueness:.2f} unique ratio)")
    elif uniqueness >= 0.30:
        confidence += 0.15
        reasons.append(f"moderate cardinality ({uniqueness:.2f} unique ratio)")

    return IdentifierFlag(
        role=IdentifierRole.ENTITY_ID,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons),
    )


def _score_category(
    column_name: str,
    logical_type: LogicalType,
    series: pd.Series,
) -> IdentifierFlag | None:
    if logical_type not in {LogicalType.CATEGORICAL, LogicalType.STRING, LogicalType.BOOLEAN}:
        return None

    name_hit = _name_match(CATEGORY_NAME_PATTERN, column_name)
    non_null = series.dropna()
    if non_null.empty:
        return None

    unique_count = non_null.nunique()
    if unique_count <= 1:
        return None

    confidence = 0.0
    reasons: list[str] = []

    if name_hit:
        confidence += 0.45
        reasons.append("column name matches category/status patterns")
    if unique_count <= 50:
        confidence += 0.35
        reasons.append(f"low cardinality ({unique_count} distinct values)")

    if confidence < 0.40:
        return None

    return IdentifierFlag(
        role=IdentifierRole.CATEGORY,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons),
    )


def detect_identifiers(
    column_name: str,
    logical_type: LogicalType,
    series: pd.Series,
) -> list[IdentifierFlag]:
    """Flag potential identifier roles for a single column."""
    detectors = (
        _score_transaction_id,
        _score_timestamp,
        _score_amount,
        _score_entity_id,
        _score_category,
    )

    flags: list[IdentifierFlag] = []
    for detector in detectors:
        try:
            flag = detector(column_name, logical_type, series)
        except (TypeError, ValueError, ArithmeticError):
            continue
        if flag is not None:
            flags.append(flag)

    flags.sort(key=lambda item: item.confidence, reverse=True)
    return flags
