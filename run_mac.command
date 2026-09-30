#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "[PPT Studio] 처음 실행: 설치 중..."
  python3 -m venv .venv || { echo "Python 3.10 이상을 설치해 주세요"; exit 1; }
fi
source .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt
python app.py
