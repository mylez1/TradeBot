import json
import time
from pathlib import Path
from typing import Any

import config


_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOG_DIR = _REPO_ROOT / "logs"
_TRADES_PATH = _LOG_DIR / f"trades_{config.STRATEGY_VERSION}.jsonl"
_METRICS_PATH = _LOG_DIR / "metrics.jsonl"


def _ensure_log_dir() -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Never crash the bot due to logging.
        return


def _write_jsonl(path: Path, obj: dict[str, Any]) -> None:
    try:
        _ensure_log_dir()
        if "ts" not in obj:
            obj["ts"] = float(time.time())
        line = json.dumps(obj, separators=(",", ":"), sort_keys=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Never crash the bot due to logging.
        return


def log_trade(event: dict[str, Any]) -> None:
    event.setdefault("strategy_version", config.STRATEGY_VERSION)
    _write_jsonl(_TRADES_PATH, event)


def log_metrics(metrics: dict[str, Any]) -> None:
    _write_jsonl(_METRICS_PATH, metrics)

