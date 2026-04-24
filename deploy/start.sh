#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}/trade-engine"
bun install

cd "${ROOT_DIR}"
python bot/main.py --live

