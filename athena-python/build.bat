@echo off
REM ATHENA Python v1 — build dist\Athena.exe on Windows (run once).
REM PyInstaller cannot cross-build: this must run on the laptop.
REM Needs Python 3.12+ on PATH.
setlocal
python --version >nul 2>&1 || (echo Python 3.12+ is required on PATH. & exit /b 1)
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pywin32 pyautogui
pyinstaller --name Athena --onefile --windowed --paths src ^
  --hidden-import edge_tts --hidden-import vosk ^
  --collect-all PySide6 main.py
echo.
echo Built: dist\Athena.exe
endlocal
