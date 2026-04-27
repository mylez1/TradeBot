#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
PID_FILE="${LOG_DIR}/bot.pid"
LOG_FILE="${LOG_DIR}/bot.log"

mkdir -p "${LOG_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  PID="$(cat "${PID_FILE}")"
  if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
    echo "TradeBot already running with PID ${PID}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

cd "${ROOT_DIR}/trade-engine"
if [[ ! -d "node_modules" ]]; then
  bun install
else
  echo "trade-engine dependencies already installed"
fi

cd "${ROOT_DIR}"
if [[ -f "requirements.txt" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  "${PYTHON_BIN}" -m pip install -r requirements.txt
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

echo "Starting TradeBot in PAPER mode..."
nohup "${PYTHON_BIN}" -u bot/main.py >> "${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"
echo "TradeBot started with PID $(cat "${PID_FILE}")"
echo "Logs: ${LOG_FILE}"

