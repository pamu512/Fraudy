from __future__ import annotations

import io
import logging
import os
import uuid
from typing import Any, Callable, Literal

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from app.engines.anomaly_detection import run_anomaly_detection
from app.engines.benfords_law import run_benfords_law
from app.engines.column_significance import run_column_significance
from app.engines.types import EngineResult
from app.profiling.profiler import ProfilingError, profile_dataframe, profile_upload

logger = logging.getLogger(__name__)

EngineRunner = Callable[[pd.DataFrame], EngineResult]
API_KEY_NAME = "X-Fraudy-Token"
DEFAULT_API_KEY = "super-secret-local-token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

SESSION_CACHE: dict[str, dict[str, Any]] = {}

ENGINES: dict[str, EngineRunner] = {
    "benfords_law": run_benfords_law,
    "anomaly_detection": run_anomaly_detection,
    "column_significance": run_column_significance,
}


class DatasetPayload(BaseModel):
    headers: list[str] = Field(..., min_length=1)
    rows: list[list[Any]] = Field(default_factory=list)
    range_address: str | None = None

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: list[str]) -> list[str]:
        cleaned = [h.strip() for h in value if h is not None and str(h).strip()]
        if not cleaned:
            raise ValueError("At least one non-empty header is required.")
        return cleaned


class AnalyzeRequest(DatasetPayload):
    """JSON payload for multi-engine analysis."""


class BenfordsLawRequest(DatasetPayload):
    """JSON payload for Benford's Law chi-square analysis on a numerical column."""

    column: str | None = Field(
        default=None,
        description=(
            "Designated numerical column to test. When omitted, all numeric "
            "columns with sufficient data are evaluated."
        ),
    )


class AnomalyDetectionRequest(DatasetPayload):
    """JSON payload for per-row anomaly detection."""

    method: Literal["isolation_forest", "rolling_zscore"] = Field(
        default="isolation_forest",
        description="Detection strategy: multivariate Isolation Forest or rolling Z-score.",
    )
    columns: list[str] | None = Field(
        default=None,
        description="Optional numeric columns to include. Defaults to all numeric columns.",
    )
    zscore_threshold: float = Field(
        default=3.0,
        gt=0,
        description="Rolling Z-score threshold (used when method=rolling_zscore).",
    )
    window_size: int = Field(
        default=20,
        ge=3,
        description="Rolling window size (used when method=rolling_zscore).",
    )


class AnalyzeResponse(BaseModel):
    engines: list[dict[str, Any]]
    summary: dict[str, Any]
    range_address: str | None = None


class EngineResponse(BaseModel):
    engine: str
    hits: list[dict[str, Any]]
    summary: dict[str, Any]
    range_address: str | None = None
    error: str | None = None


class SessionUploadResponse(BaseModel):
    session_id: str
    total_rows: int
    profile: dict[str, Any]

class SessionAnalyzeRequest(BaseModel):
    included_columns: list[str]

class SessionAnalyzeResponse(BaseModel):
    session_id: str
    message: str


class SessionRowResult(BaseModel):
    row_index: int
    score: float
    is_anomaly: bool
    hits: list[dict[str, Any]]


class SessionResultsResponse(BaseModel):
    session_id: str
    skip: int
    limit: int
    total_rows: int
    results: list[SessionRowResult]


app = FastAPI(
    title="Fraud Analyzer API",
    description=(
        "Explainable statistical and ML fraud rule engines for spreadsheet data, "
        "plus raw dataset profiling with schema inference and correlation analysis."
    ),
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _rows_to_dataframe(headers: list[str], rows: list[list[Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=headers)

    normalized_rows: list[list[Any]] = []
    for row in rows:
        padded = list(row[: len(headers)])
        while len(padded) < len(headers):
            padded.append(None)
        normalized_rows.append(padded)

    return pd.DataFrame(normalized_rows, columns=headers)


def _build_summary(engine_results: list[EngineResult]) -> dict[str, Any]:
    total_hits = 0
    failed_hits = 0
    max_score = 0.0
    engines_run = 0
    engines_with_errors = 0

    for result in engine_results:
        if result.error:
            engines_with_errors += 1
            continue
        engines_run += 1
        for hit in result.hits:
            total_hits += 1
            if not hit.passed:
                failed_hits += 1
                max_score = max(max_score, hit.score)

    return {
        "engines_run": engines_run,
        "engines_with_errors": engines_with_errors,
        "total_rules_evaluated": total_hits,
        "failed_rules": failed_hits,
        "max_risk_score": round(max_score, 4),
        "overall_passed": failed_hits == 0 and engines_with_errors == 0,
    }


def _run_engine_safe(name: str, runner: EngineRunner, df: pd.DataFrame) -> EngineResult:
    try:
        return runner(df)
    except Exception as exc:
        logger.exception("Engine %s failed", name)
        return EngineResult(engine=name, hits=[], error=str(exc))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _build_engine_summary(result: EngineResult) -> dict[str, Any]:
    if result.error:
        return {
            "engines_run": 0,
            "engines_with_errors": 1,
            "total_rules_evaluated": 0,
            "failed_rules": 0,
            "max_risk_score": 0.0,
            "overall_passed": False,
        }
    return _build_summary([result])


def _engine_response(
    result: EngineResult,
    range_address: str | None = None,
) -> EngineResponse:
    payload = result.to_dict()
    return EngineResponse(
        engine=payload["engine"],
        hits=payload["hits"],
        summary=_build_engine_summary(result),
        range_address=range_address,
        error=payload.get("error"),
    )


def _dataframe_from_request(request: DatasetPayload) -> pd.DataFrame:
    if not request.rows:
        raise HTTPException(status_code=400, detail="No row data provided.")
    return _rows_to_dataframe(request.headers, request.rows)


def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    expected = os.getenv("FRAUDY_API_KEY", DEFAULT_API_KEY)
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="FRAUDY_API_KEY is not configured on the backend.",
        )
    if api_key != expected:
        raise HTTPException(status_code=403, detail="Unauthorized request source.")
    return api_key


@app.post(
    "/session/upload",
    response_model=SessionUploadResponse,
    dependencies=[Depends(verify_api_key)],
)
async def upload_dataset_session(
    file: UploadFile = File(..., description="Raw CSV or Excel dataset to profile globally."),
) -> dict[str, Any]:
    """
    Accepts a single raw CSV/Excel upload, compiles global statistical profiles,
    and stores the dataframe for pending anomaly detection configuration.
    """
    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    try:
        content = await file.read()
        filename = file.filename.lower()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read upload: {exc}") from exc

    try:
        if filename.endswith(".xls") or filename.endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            df = pd.read_csv(io.BytesIO(content))
            
        if df.empty:
            raise HTTPException(status_code=422, detail="Dataset contains no rows.")
            
        # Profile dataset to infer schema and flag metadata
        profile_result = profile_dataframe(df, source_filename=filename)
        
        total_rows = len(df)
        session_id = str(uuid.uuid4())
        SESSION_CACHE[session_id] = {
            "total_rows": total_rows,
            "df": df,
            "profile": profile_result.to_dict(),
            "results": None
        }
        
        return {
            "session_id": session_id,
            "total_rows": total_rows,
            "profile": profile_result.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Global ingestion failed")
        raise HTTPException(
            status_code=500,
            detail=f"Global ingestion failed: {exc}",
        ) from exc


@app.post(
    "/session/{session_id}/analyze",
    response_model=SessionAnalyzeResponse,
    dependencies=[Depends(verify_api_key)],
)
async def analyze_dataset_session(
    session_id: str,
    request: SessionAnalyzeRequest,
) -> dict[str, Any]:
    """
    Run anomaly detection and Benford's Law on the verified columns for the uploaded session.
    """
    if session_id not in SESSION_CACHE:
        raise HTTPException(status_code=404, detail="Session expired or not found.")
        
    session_data = SESSION_CACHE[session_id]
    df = session_data.get("df")
    
    if df is None:
        if session_data.get("results") is not None:
            raise HTTPException(status_code=400, detail="Session has already been analyzed.")
        raise HTTPException(status_code=500, detail="Session data missing.")
        
    try:
        total_rows = session_data["total_rows"]
        row_results = [
            {
                "row_index": i, 
                "score": 0.0, 
                "is_anomaly": False, 
                "hits": []
            } 
            for i in range(total_rows)
        ]
        
        # Run engines specifically on the requested columns
        anomaly_result = run_anomaly_detection(df, method="isolation_forest", columns=request.included_columns)
        
        # Run Benford's Law for each requested column, or global
        benford_result = None
        for col in request.included_columns:
            res = run_benfords_law(df, column=col)
            if benford_result is None:
                benford_result = res
            else:
                benford_result.hits.extend(res.hits)
        
        # Map hits back to rows
        all_hits = anomaly_result.hits + (benford_result.hits if benford_result else [])
        for hit in all_hits:
            if hit.passed or hit.rule_name.endswith("_summary") or hit.rule_name.endswith("_scan"):
                continue
            for row_idx in hit.affected_rows:
                if 0 <= row_idx < total_rows:
                    row_results[row_idx]["is_anomaly"] = True
                    row_results[row_idx]["score"] = max(row_results[row_idx]["score"], hit.score)
                    row_results[row_idx]["hits"].append(hit.to_dict())
        
        # Save results and free the dataframe to preserve memory
        SESSION_CACHE[session_id]["results"] = row_results
        SESSION_CACHE[session_id]["df"] = None
        
        return {
            "session_id": session_id,
            "message": "Analysis complete."
        }
    except Exception as exc:
        logger.exception("Session analysis failed")
        raise HTTPException(
            status_code=500,
            detail=f"Session analysis failed: {exc}",
        ) from exc


@app.get(
    "/session/{session_id}/results",
    response_model=SessionResultsResponse,
    dependencies=[Depends(verify_api_key)],
)
async def get_dataset_session_results(
    session_id: str,
    skip: int = 0,
    limit: int = 2000,
) -> dict[str, Any]:
    """
    Allows frontends to securely paginate and stream down the calculated scores
    and explainable hits without hitting memory walls or script timeout ceilings.
    """
    if session_id not in SESSION_CACHE:
        raise HTTPException(status_code=404, detail="Session expired or not found.")
        
    session_data = SESSION_CACHE[session_id]
    results_chunk = session_data["results"][skip : skip + limit]
    
    return {
        "session_id": session_id,
        "skip": skip,
        "limit": limit,
        "total_rows": session_data["total_rows"],
        "results": results_chunk
    }


@app.post("/engines/benfords-law", response_model=EngineResponse)
def benfords_law_endpoint(request: BenfordsLawRequest) -> EngineResponse:
    """
    Chi-square Benford's Law test on leading digits of a numerical column.

    Flags potential manual manipulation when the digit distribution diverges
    significantly from Benford's expected frequencies.
    """
    df = _dataframe_from_request(request)
    result = _run_engine_safe(
        "benfords_law",
        lambda frame: run_benfords_law(frame, column=request.column),
        df,
    )
    return _engine_response(result, range_address=request.range_address)


@app.post("/engines/anomaly-detection", response_model=EngineResponse)
def anomaly_detection_endpoint(request: AnomalyDetectionRequest) -> EngineResponse:
    """
    Detect anomalous transactions with Isolation Forest or rolling Z-score.

    Returns a clear anomaly score and explainable reason for each flagged row.
    """
    df = _dataframe_from_request(request)
    result = _run_engine_safe(
        "anomaly_detection",
        lambda frame: run_anomaly_detection(
            frame,
            method=request.method,
            columns=request.columns,
            zscore_threshold=request.zscore_threshold,
            window_size=request.window_size,
        ),
        df,
    )
    return _engine_response(result, range_address=request.range_address)


@app.post("/profile")
async def profile_dataset(
    file: UploadFile = File(..., description="Raw dataset file (CSV, JSON, Excel, Parquet)"),
) -> dict[str, Any]:
    """
    Profile an uploaded raw dataset with unknown column significance.

    Infers schema types, flags potential identifiers (transaction IDs, timestamps,
    amounts), runs descriptive statistics, and computes Pearson/Spearman correlation
    matrices across numeric columns.
    """
    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    try:
        content = await file.read()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read upload: {exc}") from exc

    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        result = profile_upload(content, file.filename)
    except ProfilingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected profiling failure")
        raise HTTPException(
            status_code=500,
            detail=f"Profiling failed due to an internal error: {exc}",
        ) from exc

    return result.to_dict()


@app.post("/profile/json")
def profile_from_json(request: AnalyzeRequest) -> dict[str, Any]:
    """
    Profile a dataset provided as JSON headers + rows (same shape as /analyze).

    Useful when data is already in memory from the Excel add-in without a file upload.
    """
    if not request.rows:
        raise HTTPException(status_code=400, detail="No row data provided.")

    df = _rows_to_dataframe(request.headers, request.rows)

    try:
        result = profile_dataframe(df, source_filename=request.range_address)
    except ProfilingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected profiling failure")
        raise HTTPException(
            status_code=500,
            detail=f"Profiling failed due to an internal error: {exc}",
        ) from exc

    return result.to_dict()


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    df = _dataframe_from_request(request)

    engine_results = [
        _run_engine_safe(name, runner, df) for name, runner in ENGINES.items()
    ]

    return AnalyzeResponse(
        engines=[result.to_dict() for result in engine_results],
        summary=_build_summary(engine_results),
        range_address=request.range_address,
    )
