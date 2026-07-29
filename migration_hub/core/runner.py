"""
Shared runner: executes a workflow's pipeline, collects logs to a file
and appends to the run history (JSON Lines) — one history for the whole hub.
"""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

from .registry import Workflow

HUB_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = HUB_DIR / "logs"
HISTORY_FILE = LOG_DIR / "history.jsonl"


def _append_history(record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_history(key: str | None = None, limit: int = 50) -> list[dict]:
    """Latest runs (newest first), optionally for a single workflow."""
    if not HISTORY_FILE.exists():
        return []
    records = [json.loads(line) for line in
               HISTORY_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if key:
        records = [r for r in records if r.get("workflow") == key]
    return records[::-1][:limit]


def run_workflow(wf: Workflow, params: dict, progress=None) -> dict:
    """
    Run a workflow. `progress(msg)` is called for every log line
    (the UI hooks a live log view into it).

    Returns the history record: status, duration, log path, run() result.
    """
    started = datetime.now()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"{wf.key}_{started:%Y%m%d_%H%M%S}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    class _ProgressHandler(logging.Handler):
        def emit(self, rec):
            if progress:
                progress(self.format(rec))

    ui_handler = _ProgressHandler()
    ui_handler.setFormatter(fmt)
    root.addHandler(ui_handler)

    record = {"workflow": wf.key, "started": started.isoformat(timespec="seconds"),
              "params": params, "log": str(logfile)}
    try:
        module = wf.load_pipeline()
        result = module.run(params, progress=progress or (lambda m: None))
        record.update(status="ok", result=result)
    except Exception:
        logging.getLogger("hub").error("Error:\n%s", traceback.format_exc())
        record.update(status="error", error=traceback.format_exc())
    finally:
        record["duration_s"] = round((datetime.now() - started).total_seconds(), 1)
        root.removeHandler(file_handler)
        root.removeHandler(ui_handler)
        file_handler.close()
        _append_history(record)
    return record
