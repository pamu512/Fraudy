from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request

app = FastAPI(
    title="Fraudy Dummy Analyze Server",
    description="Diagnostic /analyze endpoint for isolating client payload issues.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Payload must be a JSON object.")

    headers = payload.get("headers", [])
    rows = payload.get("rows", [])
    range_address = payload.get("range_address", "Unknown")

    if not isinstance(headers, list):
        raise HTTPException(status_code=422, detail="'headers' must be a list.")
    if not isinstance(rows, list):
        raise HTTPException(status_code=422, detail="'rows' must be a list.")

    print("\n" + "=" * 40)
    print(f"RECEIVED BATCH: {range_address}")
    print(f"Headers: {headers}")
    print(f"Row Count: {len(rows)}")
    print("=" * 40)

    hits: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index % 2 != 0:
            continue

        hits.append(
            {
                "rule_name": f"anomaly_row_{index}",
                "passed": False,
                "score": 0.85,
                "reason": f"Dummy anomaly for row {index}; received row={row!r}",
                "affected_columns": headers,
                "affected_rows": [index],
                "details": {
                    "anomaly_score": 0.85,
                    "source": "dummy_test_server",
                    "local_chunk_index": index,
                },
            }
        )

    return {
        "engines": [
            {
                "engine": "anomaly_detection",
                "hits": hits,
            }
        ],
        "summary": {
            "engines_run": 1,
            "engines_with_errors": 0,
            "total_rules_evaluated": len(rows),
            "failed_rules": len(hits),
            "max_risk_score": 0.85 if hits else 0.0,
            "overall_passed": len(hits) == 0,
        },
        "range_address": range_address,
        "echo": {
            "headers": headers,
            "row_count": len(rows),
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
