"""Shared batched CSV export with progress logging.

Big frames are written in batches so the log (and the live log view in
Streamlit) shows export progress instead of going silent for minutes:
  * <= 10k rows  -> single write, no batching
  * <= 1M rows   -> batches of 10k
  * >  1M rows   -> batches of 100k
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("hub.export")

SMALL_FILE_ROWS = 10_000
BATCH_SMALL = 10_000
BATCH_LARGE = 100_000
LARGE_FILE_ROWS = 1_000_000


def batch_size(n_rows: int) -> int | None:
    """Batch size for a frame of n_rows; None = write in one go."""
    if n_rows <= SMALL_FILE_ROWS:
        return None
    return BATCH_LARGE if n_rows > LARGE_FILE_ROWS else BATCH_SMALL


def write_csv_batched(df: pd.DataFrame, path, *, sep: str = ",",
                      encoding: str = "utf-8", lineterminator: str = "\r\n",
                      index: bool = False, label: str | None = None,
                      logger: logging.Logger | None = None) -> None:
    """Write df to CSV, in progress-logged batches when it is large."""
    logger = logger or log
    label = label or Path(path).name
    n = len(df)
    bs = batch_size(n)

    if bs is None:
        df.to_csv(path, sep=sep, index=index, encoding=encoding,
                  lineterminator=lineterminator)
        logger.info("Saved %s (%s rows)", path, n)
        return

    # BOM (utf-8-sig) only in the first batch — appends must not repeat it
    append_enc = "utf-8" if encoding.lower().replace("_", "-") == "utf-8-sig" \
        else encoding
    total = (n + bs - 1) // bs
    logger.info("[%s] exporting %s rows in %s batches of %s…", label, n, total, bs)
    for i in range(total):
        chunk = df.iloc[i * bs:(i + 1) * bs]
        chunk.to_csv(path, sep=sep, index=index,
                     mode="w" if i == 0 else "a", header=(i == 0),
                     encoding=encoding if i == 0 else append_enc,
                     lineterminator=lineterminator)
        done = min((i + 1) * bs, n)
        logger.info("[%s] export progress: %s/%s rows (%d%%, batch %s/%s)",
                    label, done, n, done * 100 // n, i + 1, total)
    logger.info("Saved %s (%s rows)", path, n)
