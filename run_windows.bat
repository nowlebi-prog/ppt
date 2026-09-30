@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist .venv (
  echo [PPT Studio] 처음 실행: 설치 중...
  %PY% -m venv .venv || (echo Python 3.10 이상을 설치해 주세요: https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt
python app.py
pause
