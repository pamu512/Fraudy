from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuleHit:
    """Single explainable rule evaluation from an engine."""

    rule_name: str
    passed: bool
    score: float
    reason: str
    affected_columns: list[str] = field(default_factory=list)
    affected_rows: list[int] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "passed": self.passed,
            "score": round(self.score, 4),
            "reason": self.reason,
            "affected_columns": self.affected_columns,
            "affected_rows": self.affected_rows,
            "details": self.details,
        }


@dataclass
class EngineResult:
    """Aggregated output from one fraud rule engine."""

    engine: str
    hits: list[RuleHit] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "engine": self.engine,
            "hits": [hit.to_dict() for hit in self.hits],
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload
