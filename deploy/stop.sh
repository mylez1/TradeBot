#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${ROOT_DIR}/logs/bot.pid"

stop_pid() {
  local pid="$1"
  if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
    return 0
  fi

  echo "Stopping TradeBot PID ${pid}..."
  kill "${pid}" 2>/dev/null || true

  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done

  echo "TradeBot PID ${pid} did not stop cleanly; forcing..."
  kill -9 "${pid}" 2>/dev/null || true
}

if [[ -f "${PID_FILE}" ]]; then
  stop_pid "$(cat "${PID_FILE}")"
  rm -f "${PID_FILE}"
fi

PIDS="$(pgrep -f "[p]ython.*bot/main.py" || true)"
if [[ -n "${PIDS}" ]]; then
  for pid in ${PIDS}; do
    stop_pid "${pid}"
  done
fi

echo "TradeBot stopped."
