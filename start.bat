@echo off
REM Starts Inkventory locally: API on http://localhost:8000, web app on http://localhost:5173
REM Uses backend\.env if present; without it the app runs in demo mode (no API key needed).
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Setting up Python environment...
  py -3.11 -m venv backend\.venv || python -m venv backend\.venv
  backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
)
if not exist "frontend\node_modules" (
  echo Installing frontend packages...
  pushd frontend & call npm install & popd
)
if not exist "frontend\dist\index.html" (
  pushd frontend & call npm run build & popd
)

start "Inkventory API" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"
start "Inkventory Web" cmd /k "cd /d "%~dp0frontend" && npx vite preview --port 5173 --strictPort"

echo Waiting for the app to start...
timeout /t 15 /nobreak >nul
start "" http://localhost:5173
