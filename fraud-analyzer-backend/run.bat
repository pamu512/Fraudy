@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if %errorlevel% neq 0 (
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo Error: Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Starting Fraud Analysis Service on http://127.0.0.1:8000
echo Press Ctrl+C to stop the service.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
endlocal
