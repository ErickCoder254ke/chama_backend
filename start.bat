@echo off

echo Starting ChamaKe Backend Server...

REM Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Start server
echo Starting FastAPI server on http://localhost:8000
uvicorn server:app --reload --host 0.0.0.0 --port 8000
