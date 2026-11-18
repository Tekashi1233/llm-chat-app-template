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
REM --collect-all (not --hidden-import): vosk / edge_tts / sounddevice
REM ship native libraries + data files the frozen exe needs.
REM --hidden-import only pulls the top-level module and silently drops
REM the rest — a build made that way has no voice at all (the mic then
REM reports "the speech recognition engine could not start").
REM win32crypt / pywintypes: the DPAPI key decryptor (config.SecureStore
REM imports win32crypt lazily). Without it the app silently behaves as
REM "no site key stored" and falls back to Ollama, so chat shows the
REM misleading "Ollama isn't running" message. --collect-all win32
REM covers the win32 package; the two hidden imports cover the
REM top-level extension modules PyInstaller cannot collect as a package.
pyinstaller --name Athena --onefile --windowed --paths src ^
  --collect-all vosk --collect-all edge_tts --collect-all sounddevice ^
  --collect-all win32 --hidden-import win32crypt --hidden-import pywintypes ^
  --collect-all PySide6 main.py
echo.
echo Built: dist\Athena.exe
endlocal
