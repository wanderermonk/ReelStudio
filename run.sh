#!/usr/bin/env bash
# Reel Studio launcher. First run sets everything up (about 1–2 minutes), later runs start in seconds.
set -e
cd "$(dirname "$0")"
chmod +x run.sh 2>/dev/null || true
PORT="${PORT:-7860}"

PY=""
for c in python3.12 python3.11 python3.13 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9+ is needed. Install it from https://www.python.org/downloads/ and run ./run.sh again."; exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First-time setup: creating a private Python environment…"
  "$PY" -m venv .venv
fi
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing the app's parts (Flask, ffmpeg, numpy, Pillow)…"
  # Ignore any custom pip index on this Mac (e.g. an NVIDIA mirror) and use the public PyPI.
  export PIP_CONFIG_FILE=/dev/null
  unset PIP_INDEX_URL PIP_EXTRA_INDEX_URL PIP_FIND_LINKS
  PIPI="--index-url https://pypi.org/simple --disable-pip-version-check"
  .venv/bin/python -m pip install --quiet $PIPI --upgrade pip
  .venv/bin/python -m pip install --quiet $PIPI -r requirements.txt
  touch .venv/.installed
fi

if [ -n "$COMFY_API_KEY" ]; then echo "Using COMFY_API_KEY from the environment."; fi
# Already running? Just open it.
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then
  echo "Reel Studio is already running at http://127.0.0.1:$PORT"
  command -v open >/dev/null && open "http://127.0.0.1:$PORT"
  exit 0
fi

# Open the browser only once the app is answering (avoids "Failed to open page").
(
  for i in $(seq 1 60); do
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then
      if command -v open >/dev/null; then open "http://127.0.0.1:$PORT"; fi
      exit 0
    fi
    sleep 1
  done
  echo "The app did not start within 60 seconds. Check the messages above."
) &
PORT="$PORT" exec .venv/bin/python app.py
