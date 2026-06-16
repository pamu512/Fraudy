# Fraudy

Fraudy is a spreadsheet fraud analysis toolkit: a FastAPI backend with explainable statistical/ML rule engines, plus an Excel Office Add-in that sends your selected range for analysis.

I built this for solo use — whitebox rules you can read and trust, not a black-box score.

## Why Deploy an Architecture Like Fraudy?

Fraudy is designed as a foundational toolkit for fraud analysis that bridges the gap between powerful statistical techniques (Python/Pandas) and the environment where analysts actually work (Excel/Google Sheets). While currently a prototype, it demonstrates several key architectural principles:

### 1. Inspectable Evidence (Whitebox Over Blackbox)
Instead of returning an opaque "98% Fraud Risk" score, Fraudy is built around **explainability**. 
* **The Reality:** When an anomaly is detected (e.g., via Isolation Forest), the backend calculates the specific `z_score_contributions` that pushed the row into outlier territory. 
* **The Benefit:** Analysts receive actionable reasons (e.g., "Amount = 5000 (Z: +3.2), Fee = 0 (Z: -2.1)") directly in their spreadsheet, allowing them to verify the machine's logic manually rather than trusting it blindly.

### 2. Spreadsheet-Native Workflows (Reducing Context Switching)
Fraud investigators live in spreadsheets. Forcing them to export CSVs, upload them to a separate web portal, run a job, and download the results breaks their flow.
* **The Reality:** The Fraudy architecture integrates directly into Excel (via Office.js) and Google Sheets (via Apps Script). 
* **The Benefit:** Analysts select their data and click a button. The risk scores and explanations are written *directly back into the grid* with conditional formatting. This eliminates "swivel-chair" operational fatigue and keeps the data where it belongs.

### 3. Stateful Session Processing (Preserving Global Context)
Large datasets cannot be easily sent in a single chunked JSON request without hitting memory walls or losing the "global context" required by statistical models.
* **The Reality:** The architecture uses a `POST /session/upload` endpoint to ingest the raw file into an in-memory Pandas dataframe, runs the global Isolation Forest once, and stores the results. Clients then loop `GET /session/{session_id}/results` to paginate the precomputed scores.
* **The Benefit:** This bypasses Apps Script execution timeouts and payload limits, ensures models like Isolation Forest have the entire dataset to establish a baseline, and keeps the memory footprint on the client side negligible.
* *Caveat:* The current implementation uses an **in-memory cache** (`SESSION_CACHE`), meaning session data is lost if the FastAPI server restarts. A production deployment would swap this for Redis or a dedicated database.

### 4. Local-First Data Sovereignty (For Sensitive PII)
Fraud data is highly sensitive. Cloud-SaaS tools require uploading PII to third-party servers.
* **The Reality:** The Python backend is designed to run natively on the analyst's own laptop using simple shell scripts (`setup.bat` / `run.bat`).
* **The Benefit:** The data never leaves the analyst's machine. The Excel Add-in communicates purely over `localhost`, ensuring strict compliance with data sovereignty and InfoSec requirements.
* *Caveat:* The Google Sheets integration inherently requires the data to exist in Google's cloud and necessitates a tunnel (like ngrok) to reach the local backend.

## Project layout

```
fraud-analyzer-backend/   FastAPI + Pandas + Scikit-Learn engines
fraud-analyzer-frontend/    Excel task pane add-in (TypeScript / Office.js)
google-apps-script/        Google Sheets Apps Script integration
docker-compose.yml          Run the backend in Docker
```

## Backend

### Run natively (recommended)

Docker is optional. For analyst laptops, I run the backend natively in a lightweight Python virtual environment.

Windows:

```bat
cd fraud-analyzer-backend
setup.bat
run.bat
```

macOS / Linux:

```bash
cd fraud-analyzer-backend
chmod +x setup.sh run.sh
./setup.sh
./run.sh
```

The service starts at [http://127.0.0.1:8000](http://127.0.0.1:8000). API docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Use Python 3.10, 3.11, or 3.12. Python 3.13+ may not have compatible wheels for the scientific packages yet.

For session-based upload endpoints, set an API token before exposing the backend through any tunnel:

```bash
export FRAUDY_API_KEY="replace-with-a-long-random-token"
```

On Windows PowerShell:

```powershell
$env:FRAUDY_API_KEY = "replace-with-a-long-random-token"
```

### Manual local run

```bash
cd fraud-analyzer-backend
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r app/requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Run with Docker (optional)

From the repo root:

```bash
docker compose up --build
```

Docker is useful for reproducible developer environments, but it is not required for normal analyst usage.

### API

**`GET /health`** — liveness check

**`POST /analyze`** — run all engines on sheet data

**`POST /engines/benfords-law`** — Benford chi-square test on a designated numeric column

**`POST /engines/anomaly-detection`** — per-row anomaly detection (Isolation Forest or rolling Z-score)

Request body:

```json
{
  "headers": ["Amount", "Vendor", "Date"],
  "rows": [
    ["100.50", "ACME Corp", "2024-01-15"],
    ["250.00", "ACME Corp", "2024-01-16"]
  ],
  "range_address": "A1:C2"
}
```

Response includes per-engine explainable hits (`rule_name`, `passed`, `score`, `reason`, `affected_columns`, `affected_rows`, `details`) plus a summary.

Benford's Law example:

```bash
curl -s http://localhost:8000/engines/benfords-law \
  -H "Content-Type: application/json" \
  -d '{
    "headers": ["Amount"],
    "rows": [["123"],["456"],["789"],["111"],["222"],["333"],["444"],["555"],["666"],["777"],["888"],["999"]],
    "column": "Amount"
  }' | python -m json.tool
```

Anomaly detection example:

```bash
curl -s http://localhost:8000/engines/anomaly-detection \
  -H "Content-Type: application/json" \
  -d '{
    "headers": ["Amount", "Fee"],
    "rows": [[100,1],[102,1],[98,1],[500,50],[101,1],[99,1],[103,1],[97,1],[101,1],[100,1]],
    "method": "rolling_zscore",
    "window_size": 5,
    "zscore_threshold": 2.5
  }' | python -m json.tool
```

Each flagged row returns an `anomaly_score` and human-readable `reason` in the hits list.

**`POST /profile`** — upload a raw dataset file (CSV, TSV, JSON, Excel, Parquet) for automatic profiling

Returns schema inference, identifier flags, descriptive statistics, and Pearson/Spearman correlation matrices.

**`POST /profile/json`** — same profiling pipeline using JSON `{ headers, rows }` (compatible with Excel add-in data)

Example upload:

```bash
curl -s -X POST http://localhost:8000/profile \
  -F "file=@transactions.csv" | python -m json.tool
```

### Session-based upload API

For larger datasets, use the stateful session flow instead of chunked JSON. The backend accepts one CSV file, fits global anomaly baselines once, stores score results in an in-memory cache, and lets clients page through precomputed scores.

These endpoints require the `X-Fraudy-Token` header. The expected token comes from `FRAUDY_API_KEY`; if unset, the local development default is `super-secret-local-token`.

**`POST /session/upload`** — upload a CSV, compile global statistical profiles, and return the inferred schema.

```bash
curl -s -X POST http://127.0.0.1:8000/session/upload \
  -H "X-Fraudy-Token: super-secret-local-token" \
  -F "file=@transactions.csv" | python -m json.tool
```

Example response:

```json
{
  "session_id": "7f5f5e7d-9e24-4df6-8f83-75a62dcd43e4",
  "total_rows": 50000,
  "profile": {
    "columns": [
      { "name": "Tx_ID", "logical_type": "metadata_id" },
      { "name": "Amount", "logical_type": "float" }
    ]
  }
}
```

**`POST /session/{session_id}/analyze`** — run engines on verified columns

```bash
curl -s -X POST "http://127.0.0.1:8000/session/7f5f5e7d-9e24-4df6-8f83-75a62dcd43e4/analyze" \
  -H "X-Fraudy-Token: super-secret-local-token" \
  -H "Content-Type: application/json" \
  -d '{"included_columns": ["Amount"]}'
```

**`GET /session/{session_id}/results`** — page through score results

```bash
curl -s "http://127.0.0.1:8000/session/7f5f5e7d-9e24-4df6-8f83-75a62dcd43e4/results?skip=0&limit=2000" \
  -H "X-Fraudy-Token: super-secret-local-token" | python -m json.tool
```

Session data is stored in memory (`SESSION_CACHE`) by default, meaning sessions are cleared if the FastAPI server restarts.

### Profiling modules

| Module | Responsibility |
|--------|----------------|
| `schema_mapper` | Infers logical types (integer, float, datetime, categorical, string) |
| `identifier_detector` | Flags transaction IDs, timestamps, amounts, entity IDs, categories |
| `descriptive_stats` | Type-aware summary statistics per column |
| `correlation_matrix` | Pearson + Spearman matrices and strong dependency pairs |

### Engines

| Engine | What it checks |
|--------|----------------|
| `benfords_law` | First-digit distribution vs Benford's Law on numeric columns |
| `anomaly_detection` | Isolation Forest outliers with per-feature z-score contributions |
| `column_significance` | Variance, coefficient of variation, and diversity per column |

Each engine is modular — `main.py` orchestrates them without shared state.

## Google Sheets

Yes, the same FastAPI backend works with Google Sheets through the standalone Apps Script in `google-apps-script/Code.gs`.

### How it works

1. The Google Sheet adds a custom **Fraud Tool** menu.
2. **Run Fraud Analysis** reads the active sheet with `getValues()`.
3. Empty columns are stripped out before sending data.
4. The sheet data is serialized once into CSV.
5. The script uploads that CSV to `/session/upload` with `UrlFetchApp.fetch()`.
6. Fraudy responds with the detected schema. A confirmation dialog lets the analyst proceed.
7. The script asks Fraudy to `/session/{session_id}/analyze` the confirmed columns.
8. The script pages `/session/{session_id}/results` for precomputed global scores.
9. Returned scores and reasons are appended as new **Risk Score** and **Reason** columns.

The upload uses multipart form data, not chunked JSON:

```text
POST /session/upload
X-Fraudy-Token: <configured token>
file=@sheet.csv
```

### Setup

1. Open a Google Sheet.
2. Go to **Extensions → Apps Script**.
3. Paste the contents of `google-apps-script/Code.gs`.
4. Save the project and reload the sheet.
5. Use **Fraud Tool → Set API Endpoint** to configure the backend base URL.
6. Use **Fraud Tool → Set API Token** to configure the `X-Fraudy-Token` value.
7. Use **Fraud Tool → Run Session Analysis**.

### Local backend access

Google Apps Script runs in Google's cloud, so it cannot call `http://127.0.0.1:8000` or `http://localhost:8000` on your laptop directly.

For local testing, expose the native FastAPI backend with a tunnel such as ngrok:

```bash
ngrok http 8000
```

Then set the Apps Script endpoint to:

```text
https://<your-ngrok-id>.ngrok-free.app
```

For production or team usage, deploy the backend to an HTTPS endpoint and configure that URL through **Fraud Tool → Set API Endpoint**.

### Session note

The Apps Script uses the session API, so the backend fits one global Isolation Forest instead of recalculating separate models on row chunks. This preserves global context and avoids Apps Script JSON serialization limits.

## Frontend (Excel Add-in)

### Prerequisites

- Node.js 18+
- Excel desktop (Microsoft 365 or Excel 2016+)
- Backend running on port 8000

### Dev setup

```bash
cd fraud-analyzer-frontend
npm install
npm run dev
```

This starts webpack dev server at **https://localhost:3000** with dev HTTPS certificates (you may be prompted to trust `office-addin-dev-certs` once).

### Sideload in Excel

1. Start the backend (`docker compose up` or local uvicorn).
2. Start the frontend dev server (`npm run dev`).
3. In Excel: **Insert → Add-ins → My Add-ins → Upload My Add-in**
4. Choose `fraud-analyzer-frontend/manifest.xml` (or `dist/manifest.xml` after build).
5. Open the **Fraud Analyzer** task pane from the Home ribbon.
6. Confirm the API base URL and token in the task pane.
7. Select a range with headers + data, click **Refresh Selection**, then **Analyze & Write Results**.

The task pane uploads the selection as CSV to `/session/upload`, pages scores from `/session/{session_id}/results`, and writes a **Risk Score** and **Reason** column back into Excel with conditional formatting. Use `http://localhost:8000` as the API base URL for local desktop testing.

### Production build

```bash
cd fraud-analyzer-frontend
npm run build
```

Output lands in `dist/`. Update `manifest.xml` URLs if you host the add-in somewhere other than `https://localhost:3000`.

## Notes

- CORS is open on the backend for local add-in development.
- The add-in needs **ReadWriteDocument** permission to read your selection and write risk scores back to the workbook.
- Icon assets live in `fraud-analyzer-frontend/assets/` — replace with your own branding before publishing.

## Quick smoke test (curl)

```bash
curl -s http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "headers": ["Amount"],
    "rows": [["123"], ["456"], ["789"], ["111"], ["222"], ["333"], ["444"], ["555"], ["666"], ["777"], ["888"], ["999"]]
  }' | python -m json.tool
```

You should see results from all three engines with explainable rule hits.