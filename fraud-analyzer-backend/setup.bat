@echo off
setlocal
cd /d "%~dp0"

set "VENV_DIR=.venv"

py -3.11 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3.11"
) else (
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo Error: Python is not installed or not in PATH.
        echo Install Python 3.11 or 3.12, then run this file again.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=python"
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Fraudy requires Python 3.10, 3.11, or 3.12.
    echo Python 3.13+ may not have compatible scientific package wheels.
    %PYTHON_CMD% --version
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating lightweight virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo Error: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo Error: Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Installing backend dependencies...
python -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo Error: Failed to upgrade pip.
    pause
    exit /b 1
)

python -m pip install -r app\requirements.txt
if %errorlevel% neq 0 (
    echo Error: Failed to install backend dependencies.
    pause
    exit /b 1
)

echo Setup complete.
endlocal
