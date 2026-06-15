#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x ".venv/bin/python" ]]; then
  ./setup.sh
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

echo "Starting Fraud Analysis Service on http://127.0.0.1:8000"
echo "Press Ctrl+C to stop the service."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
