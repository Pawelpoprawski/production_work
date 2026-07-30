"""
GMIS — "LOAD GMIS TO DB" rewritten from the Alteryx workflow (gwm.txt export).

Flow (Alteryx tool IDs in parentheses):
  * ~100 GMIS export files `GMIS DATA\\All\\*.txt` (4) + schema file
    "Empty on purpose - don't delete.txt" (3, columns only) -> union (25/26)
  * Unique on ALL data columns across ALL files (23) — implemented as a
    global row-hash set so the ~100M rows are never in memory at once
  * numbers: strip thousands commas (19/16), cast to float (18/22)
  * dimensions: empty or "0" -> "-" (21), trim whitespace (20)
  * outputs (yxdb replaced by pipe-delimited CSV):
      - 0. GWM RAW GMIS.csv                (1)  — consolidated raw rows
      - temps/Current GMIS grouping.csv    (27 -> 5) — unique grouping/account/month
      - temps/Brokerage per account.csv    (29 -> 6) — Rev. Sub Type contains "Brokerage"
      - temps/GMIS Mandates per account.csv(30 -> 31 -> 7) — mandate rollup, IA >= 1
      - Compare1.csv (14 -> 9) / Compare2.csv (15 -> 8) — reconciliation of the
        consolidated sums (28 -> 10) vs GWM Grouped for Check.txt (2 -> 16 -> 11 -> 12)

Files are processed one at a time in chunks (streaming append) — memory use
is bounded by the chunk size, not the total row count.

Hub contract: run(params, progress) -> {"outputs": [...], "checks": [...],
"summary": [...]}; get_inputs/get_outputs for the UI tables.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("gmis")

CSV_SEP = "|"
CSV_ENCODING = "utf-8-sig"
CHUNK_ROWS = 200_000
COMPARE_TOLERANCE = 0.5  # abs difference treated as rounding noise

# ---------------------------------------------------------------- columns
ID_COLS = ["Year (YYYY)", "Month (YYYYMM)", "Synthetic Grouping ID (latest)",
           "R_ID", "ID 1 (Account)"]

MAND_COLS = ["Mand. Fam. Name", "Mand. Fam. ID", "Mand. Class  ID",
             "Mand. Class Name", "Mand. Type Name", "Mand. Type ID",
             "Mand. Line Name", "Mand. Line ID"]
PROD_COLS = ["Prod. Fam. Name", "Prod. Fam. ID", "Prod. Class ID",
             "Prod. Class Name", "Prod. Type Name", "Prod. Type ID",
             "Prod. Line Name", "Prod. Line ID"]
REV_COLS = ["Rev. Fam. Name", "Rev. Fam. ID", "Rev. Class Name",
            "Rev. Class ID", "Rev. Type Name", "Rev. Type ID",
            "Rev. Line Name", "Rev. Line ID",
            "Rev. Sub Type Name", "Rev. Sub Type ID"]
BASE_COLS = ["Base RT Name", "Base RT ID", "Base RS ID", "Base RS Name"]
HIER_COLS = MAND_COLS + PROD_COLS + REV_COLS + BASE_COLS

MEASURE_COLS = [
    "Total assets  incl. shift impact (CA10035)",
    "Net Client Liabilities (AL21245)",
    "NNM after manual adj. (CA10505)",
    "Net New Loans aft. man. corr. (CA50112)",
    "Net rev. aft. man. adj. incl. shift impact (CA50480)",
    "FGA TOTAL ASSETS  INCL SHIFTS IMPACTS (CA30296)",
    "NNFGA incl. manual adjustments incl. UBS Advice Light (CA30112)",
    "Credit Risk Costs (RTN32000)",
    "Gross rev. aft. man. adj. incl. shift impact (CA10950)",
    "Interest incl. shift impact (CA10484)",
    "Interest income product related (incl. shift impact) (CA20178)",
    "Recurring Fees incl. shift impact (CA10485)",
    "Transaction Revenues incl. shift impact  (CA10486)",
    "LRD (BF21300)",
    "RWA (BF21295)",
    "Other Income incl. shift impact (CA10418)",
    "NN Mandates incl. manual adjustments incl. UBS Advice Light (CA66395)",
    "NN UBS Advice Advanced / Premium incl. manual adjustments (CA66392)",
    "NN UBS Advice incl. manual adjustments (CA66394)",
    "NN UBS Advice Light incl. manual adjustments (CA66396)",
    "NET NEW UBS MANAGE INCL ATA (CA11270)",
    "NN Money incl. Dividends & Interest (CA30502)",
]

DIM_COLS = ID_COLS + HIER_COLS
ALL_COLS = DIM_COLS + MEASURE_COLS  # dedup key = every data column (Unique 23)

# quick total check reconciles only these key measures
QUICK_MEASURES = [
    "Net rev. aft. man. adj. incl. shift impact (CA50480)",
    "Gross rev. aft. man. adj. incl. shift impact (CA10950)",
    "Total assets  incl. shift impact (CA10035)",
    "NNM after manual adj. (CA10505)",
]


class Config:
    def __init__(self):
        root = Path(os.environ.get("UBS_DATA_ROOT",
                                   r"\\Ubsprod.msad.ubs.net\groupshares"))
        gfo = root / "CHE" / "UHNWONEBANKC" / "GFO" / "GFO OneBank Analytics"
        gmis_data = gfo / "005_Projects" / "004_Python" / "GMIS DATA"
        database = gfo / "005_Projects" / "004_Python" / "Alteryx" / "Database"

        out_env = os.environ.get("UBS_OUTPUT_DIR")
        out_db = Path(out_env) if out_env else database
        out_cmp = Path(out_env) if out_env else gmis_data

        self.inputs = {
            "gmis_files_dir": gmis_data / "All",
            "grouped_check": gmis_data / "GWM Grouped for Check.txt",
            # built by the "GMIS Account Filter" workflow
            "account_universe": out_db / "GMIS Account Universe.csv",
        }

        self.outputs = {
            "raw": out_db / "0. GWM RAW GMIS.csv",
            "raw_universe": out_db / "0. GWM RAW GMIS Universe.csv",
            "grouping": out_db / "temps" / "Current GMIS grouping.csv",
            "brokerage": out_db / "temps" / "Brokerage per account.csv",
            "mandates": out_db / "temps" / "GMIS Mandates per account.csv",
            "compare1": out_cmp / "Compare1.csv",
            "compare2": out_cmp / "Compare2.csv",
        }

        try:
            from core import settings as hub_settings
            self.inputs = hub_settings.apply_input_overrides("gmis", self.inputs)
            self.outputs = hub_settings.apply_output_overrides("gmis", self.outputs)
        except ImportError:
            pass  # running standalone, outside the hub


def get_inputs(params: dict) -> dict:
    return dict(Config().inputs)


def get_outputs(params: dict) -> dict:
    return dict(Config().outputs)


# ---------------------------------------------------------------- helpers
def col(df: pd.DataFrame, name: str) -> str:
    """Case/whitespace-insensitive column lookup (matches Alteryx behaviour;
    also tolerates non-breaking spaces in headers like the FGA CA30296 one)."""
    def norm(s: str) -> str:
        return " ".join(s.replace("\xa0", " ").split()).lower()
    target = norm(name)
    for c in df.columns:
        if norm(c) == target:
            return c
    raise KeyError(f"Missing column '{name}'. Available: {list(df.columns)}")


class SeenHashes:
    """Global row-dedup via 64-bit row hashes kept as sorted numpy arrays
    (memory: 8 bytes/row instead of the row itself)."""

    def __init__(self):
        self._arrays: list[np.ndarray] = []

    def filter_new(self, hashes: np.ndarray) -> np.ndarray:
        """Boolean mask of rows not seen before (and unique within the batch)."""
        first_in_batch = ~pd.Series(hashes).duplicated().to_numpy()
        new = np.ones(len(hashes), dtype=bool)
        for arr in self._arrays:
            idx = np.searchsorted(arr, hashes)
            idx[idx == len(arr)] = len(arr) - 1
            new &= arr[idx] != hashes
        mask = first_in_batch & new
        if mask.any():
            self._arrays.append(np.sort(hashes[mask]))
            if len(self._arrays) > 32:  # keep the lookup list short
                self._arrays = [np.sort(np.concatenate(self._arrays))]
        return mask

    def __len__(self) -> int:
        return sum(len(a) for a in self._arrays)


def clean_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Formulas 19/16 + selects 18/22 + formula 21 + cleanse 20 on one chunk."""
    # map real headers onto canonical names (case/NBSP-tolerant)
    rename = {}
    for name in ALL_COLS:
        try:
            real = col(df, name)
            if real != name:
                rename[real] = name
        except KeyError:
            pass
    if rename:
        df = df.rename(columns=rename)
    missing = [c for c in ALL_COLS if c not in df.columns]
    if missing:
        raise KeyError(f"Missing columns in GMIS file: {missing}")

    for c in DIM_COLS:
        s = df[c].astype(str).str.strip()
        df[c] = s.mask(s.eq("") | s.eq("0") | s.eq("nan"), "-")
    for c in MEASURE_COLS:
        df[c] = pd.to_numeric(
            df[c].astype(str).str.strip().str.replace(",", "", regex=False),
            errors="coerce")
    return df


def append_csv(df: pd.DataFrame, path: Path, first: bool) -> None:
    df.to_csv(path, sep=CSV_SEP, index=False, mode="w" if first else "a",
              header=first, encoding=CSV_ENCODING if first else "utf-8",
              lineterminator="\r\n")


# ------------------------------------------------------ quick total check
def run_quick_check(cfg: Config) -> dict:
    """Fast reconciliation: per file read ONLY Year + the 22 measures,
    strip thousands commas, sum per Year; at the end sum the per-file
    partials and compare with GWM Grouped for Check. No dedup, no RAW."""
    src_dir = Path(cfg.inputs["gmis_files_dir"])
    files = sorted(p for p in src_dir.glob("*.txt")
                   if p.name != "Empty on purpose - don't delete.txt")
    if not files:
        raise FileNotFoundError(f"No *.txt GMIS files found in {src_dir}")
    log.info("QUICK CHECK: %s files — reading only Year + %s measures, "
             "no dedup / no RAW output", len(files), len(QUICK_MEASURES))

    def norm(s: str) -> str:
        return " ".join(s.replace("\xa0", " ").split()).lower()
    wanted = {norm(c): c for c in ["Year (YYYY)"] + QUICK_MEASURES}

    run_start = time.monotonic()
    parts: list[pd.DataFrame] = []   # one small Year-grouped frame per file
    for i, path in enumerate(files, 1):
        file_start = time.monotonic()
        rows = 0
        file_parts = []
        reader = pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8-sig",
                             keep_default_na=False, na_values=[""],
                             usecols=lambda c: norm(c) in wanted,
                             chunksize=CHUNK_ROWS)
        for chunk in reader:
            rows += len(chunk)
            chunk = chunk.rename(columns={c: wanted[norm(c)] for c in chunk.columns})
            for c in QUICK_MEASURES:
                chunk[c] = pd.to_numeric(
                    chunk[c].astype(str).str.replace(",", "", regex=False),
                    errors="coerce")
            chunk["Year (YYYY)"] = chunk["Year (YYYY)"].astype(str).str.strip()
            file_parts.append(chunk.groupby("Year (YYYY)", as_index=False)
                              [QUICK_MEASURES].sum())
        file_total = (pd.concat(file_parts, ignore_index=True)
                      .groupby("Year (YYYY)", as_index=False)[QUICK_MEASURES].sum())
        parts.append(file_total)
        done_s = time.monotonic() - run_start
        log.info("QUICK CHECK: file %s/%s: %s — %s rows summed in %.1f s "
                 "| overall ETA ~%.1f min", i, len(files), path.name, rows,
                 time.monotonic() - file_start, (len(files) - i) * done_s / i / 60)

    consolidated = (pd.concat(parts, ignore_index=True)
                    .groupby("Year (YYYY)", as_index=False)[QUICK_MEASURES].sum())
    consolidated["File"] = "Consolidated"

    chk = pd.read_csv(cfg.inputs["grouped_check"], sep="\t", dtype=str,
                      encoding="utf-8-sig", keep_default_na=False,
                      na_values=[""], quoting=3)
    chk = chk.rename(columns={c: wanted[norm(c)] for c in chk.columns
                              if norm(c) in wanted})
    for c in QUICK_MEASURES:
        chk[c] = pd.to_numeric(
            chk[c].astype(str).str.replace(",", "", regex=False), errors="coerce")
    chk["Year (YYYY)"] = chk["Year (YYYY)"].astype(str).str.strip()
    chk = chk.groupby("Year (YYYY)", as_index=False)[QUICK_MEASURES].sum()
    chk["File"] = "Check"
    log.info("QUICK CHECK: grouped check loaded (%s years)", len(chk))

    compare = pd.concat([consolidated, chk], ignore_index=True)
    compare[QUICK_MEASURES] = compare[QUICK_MEASURES].round(2)
    out = Path(cfg.outputs["compare2"]).with_name("Quick total check.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    compare.to_csv(out, sep=CSV_SEP, index=False, encoding=CSV_ENCODING,
                   lineterminator="\r\n")
    log.info("Saved %s (%s rows)", out, len(compare))

    cons_t = compare[compare["File"] == "Consolidated"].set_index("Year (YYYY)")[QUICK_MEASURES]
    chk_t = compare[compare["File"] == "Check"].set_index("Year (YYYY)")[QUICK_MEASURES]
    diff = cons_t.sub(chk_t, fill_value=0).round(2)
    bad = diff.abs().gt(COMPARE_TOLERANCE)
    checks = []
    if bad.to_numpy().any():
        rows = [{"Year": y, "Measure": m,
                 "Consolidated": round(float(cons_t.get(m, pd.Series()).get(y, 0)), 2),
                 "Check": round(float(chk_t.get(m, pd.Series()).get(y, 0)), 2),
                 "Diff": float(diff.loc[y, m])}
                for y in diff.index for m in QUICK_MEASURES if bad.loc[y, m]]
        log.warning("QUICK CHECK: %s Year x Measure totals differ "
                    "(note: this mode does NOT deduplicate across files)", len(rows))
        checks.append({"name": "Quick total check", "status": "warning",
                       "message": f"Quick check: {len(rows)} Year × Measure totals "
                                  f"differ (no dedup — duplicates can inflate sums)",
                       "table": rows})
    else:
        log.info("QUICK CHECK: all totals match GWM Grouped for Check")
        checks.append({"name": "Quick total check", "status": "ok",
                       "message": "Quick check: totals per Year match "
                                  "GWM Grouped for Check — all measures OK"})
    log.info("=== QUICK CHECK DONE in %.1f min ===",
             (time.monotonic() - run_start) / 60)
    return {"outputs": [str(out)], "checks": checks,
            "summary_title": "Quick totals per File × Year",
            "summary": compare.to_dict("records")}


# ---------------------------------------------------------------- main
def run(params: dict, progress=print) -> dict:
    cfg = Config()

    def flag(key: str, default: bool = False) -> bool:
        v = params.get(key, default)
        return str(v).lower() in ("true", "1", "yes")

    if flag("quick_check"):
        log.info("=== GMIS: QUICK TOTAL CHECK ===")
        return run_quick_check(cfg)

    filter_accounts = flag("filter_accounts", True)
    do_dedup = flag("dedup", False)
    checks: list[dict] = []
    log.info("=== GMIS: LOAD GMIS TO DB (account filter: %s, dedup: %s) ===",
             "ON" if filter_accounts else "OFF", "ON" if do_dedup else "OFF")

    universe: set | None = None
    raw_key = "raw"
    if filter_accounts:
        uni_path = Path(cfg.inputs["account_universe"])
        if not uni_path.exists():
            raise FileNotFoundError(
                f"Missing account universe file: {uni_path} — run the "
                f"'GMIS Account Filter' workflow first (or untick "
                f"'Filter to Account Universe').")
        uni = pd.read_csv(uni_path, sep=CSV_SEP, dtype=str, encoding="utf-8-sig")
        universe = set(uni[col(uni, "Account")].astype(str).str.strip())
        raw_key = "raw_universe"
        log.info("Account Universe loaded: %s accounts (%s), examples: %s",
                 f"{len(universe):,}", uni_path.name, sorted(universe)[:5])

    src_dir = Path(cfg.inputs["gmis_files_dir"])
    files = sorted(p for p in src_dir.glob("*.txt")
                   if p.name != "Empty on purpose - don't delete.txt")
    if not files:
        raise FileNotFoundError(f"No *.txt GMIS files found in {src_dir}")
    log.info("Found %s GMIS files in %s", len(files), src_dir)

    for out in cfg.outputs.values():
        Path(out).parent.mkdir(parents=True, exist_ok=True)

    seen = SeenHashes()
    raw_rows = 0
    brokerage_rows = 0
    raw_first = True
    brokerage_first = True
    grouping_parts: list[pd.DataFrame] = []   # unique 4-col combinations (27)
    mandate_parts: list[pd.DataFrame] = []    # per-chunk partial groupbys (30)
    consolidated_parts: list[pd.DataFrame] = []  # per-chunk partial groupbys (10)

    run_start = time.monotonic()
    for i, path in enumerate(files, 1):
        file_rows = file_kept = 0
        file_start = time.monotonic()
        size_mb = path.stat().st_size / 1024 / 1024
        log.info("Opening file %s/%s: %s (%.1f MB)", i, len(files), path.name, size_mb)
        log.info("  [%s] reading first chunk (%s rows) — a slow network share "
                 "can make this take a while…", path.name, CHUNK_ROWS)
        est_total = None  # estimated row count, derived from the first chunk
        chunk_no = 0
        chunk_start = time.monotonic()
        reader = pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8-sig",
                             keep_default_na=False, na_values=[""],
                             chunksize=CHUNK_ROWS)
        for chunk in reader:
            chunk_no += 1
            read_s = time.monotonic() - chunk_start
            log.info("  [%s] chunk %s read: %s rows in %.1f s — cleaning…",
                     path.name, chunk_no, len(chunk), read_s)
            file_rows += len(chunk)
            if est_total is None:
                sample = chunk.head(1000)
                bytes_per_row = max(
                    len(sample.to_csv(sep="\t", index=False).encode("utf-8"))
                    / max(len(sample), 1), 1)
                est_total = max(int(path.stat().st_size / bytes_per_row),
                                len(chunk))
            # account filter BEFORE cleaning — cheap isin on the raw strings
            step = time.monotonic()
            acct_dropped = 0
            if universe is not None:
                acct_col = col(chunk, "ID 1 (Account)")
                accts = chunk[acct_col].astype(str).str.strip()
                acct_mask = accts.isin(universe)
                acct_dropped = int((~acct_mask).sum())
                if chunk_no == 1:  # per-file diagnostics on the first chunk
                    uniq_accts = accts.drop_duplicates()
                    in_uni = uniq_accts.isin(universe)
                    log.info("  [%s] account diagnostics: %s unique accounts in "
                             "chunk, %s in universe, %s outside | in-file "
                             "examples: %s | outside examples: %s",
                             path.name, len(uniq_accts), int(in_uni.sum()),
                             int((~in_uni).sum()), uniq_accts.head(3).tolist(),
                             uniq_accts[~in_uni].head(3).tolist())
                    if not in_uni.any():
                        log.warning("  [%s] NO chunk account matches the "
                                    "universe — likely an ID format mismatch "
                                    "(check the examples above)", path.name)
                    elif in_uni.all():
                        log.warning("  [%s] EVERY chunk account is in the "
                                    "universe — the filter drops nothing; "
                                    "verify the universe list is not too broad",
                                    path.name)
                chunk = chunk[acct_mask]
            filt_s = time.monotonic() - step
            if not len(chunk):
                log.info("  [%s] chunk %s: no universe accounts (%s rows "
                         "dropped) — ~%s%%", path.name, chunk_no, acct_dropped,
                         min(file_rows * 100 // est_total, 99))
                chunk_start = time.monotonic()
                continue

            step = time.monotonic()
            chunk = clean_chunk(chunk)[ALL_COLS]
            clean_s = time.monotonic() - step

            # global Unique (23): drop rows already seen in ANY file (optional)
            hash_s = dedup_s = 0.0
            if do_dedup:
                step = time.monotonic()
                hashes = pd.util.hash_pandas_object(chunk, index=False).to_numpy()
                hash_s = time.monotonic() - step
                step = time.monotonic()
                mask = seen.filter_new(hashes)
                chunk = chunk[mask]
                dedup_s = time.monotonic() - step
            file_kept += len(chunk)
            if not len(chunk):
                log.info("  [%s] chunk %s fully duplicated — ~%s%%, %s rows "
                         "processed", path.name, chunk_no,
                         min(file_rows * 100 // est_total, 99), file_rows)
                chunk_start = time.monotonic()
                continue

            chunk["FileName"] = path.name
            step = time.monotonic()
            append_csv(chunk, cfg.outputs[raw_key], raw_first)
            write_s = time.monotonic() - step
            raw_first = False
            raw_rows += len(chunk)

            # (27) unique grouping per account/month
            step = time.monotonic()
            grouping_parts.append(chunk[["Synthetic Grouping ID (latest)", "R_ID",
                                         "ID 1 (Account)", "Month (YYYYMM)"]]
                                  .drop_duplicates())
            if len(grouping_parts) > 20:
                grouping_parts = [pd.concat(grouping_parts, ignore_index=True)
                                  .drop_duplicates()]

            # (29) Brokerage rows -> streaming append
            brok = chunk[chunk["Rev. Sub Type Name"]
                         .str.contains("Brokerage", na=False)]
            if len(brok):
                append_csv(brok, cfg.outputs["brokerage"], brokerage_first)
                brokerage_first = False
                brokerage_rows += len(brok)

            # (30) mandate rollup — partial groupby per chunk
            mandate_parts.append(
                chunk.groupby(["Synthetic Grouping ID (latest)", "R_ID",
                               "ID 1 (Account)"] + MAND_COLS, as_index=False)
                     [MEASURE_COLS[0]].sum()
                     .rename(columns={MEASURE_COLS[0]: "IA"}))

            # (10) consolidated grouped sums — partial groupby per chunk
            consolidated_parts.append(
                chunk.groupby(HIER_COLS + ["Year (YYYY)"], as_index=False)
                     [MEASURE_COLS].sum())
            agg_s = time.monotonic() - step

            pct = min(file_rows * 100 // est_total, 99)
            chunk_s = time.monotonic() - chunk_start
            rate = int(len(chunk) / chunk_s) if chunk_s > 0 else 0
            file_elapsed = time.monotonic() - file_start
            eta_s = max(est_total - file_rows, 0) / max(file_rows / file_elapsed, 1)
            log.info("  [%s] chunk %s done in %.1f s (%s rows/s) — file ~%s%%, "
                     "ETA for this file ~%.0f s | %s rows in RAW so far",
                     path.name, chunk_no, chunk_s, f"{rate:,}", pct, eta_s, raw_rows)
            log.info("  [%s] chunk %s steps: read %.1f s | acct filter %.1f s "
                     "(%s dropped) | clean %.1f s | hash %.1f s | dedup %.1f s "
                     "(%s hashes seen) | write RAW %.1f s | aggregates %.1f s",
                     path.name, chunk_no, read_s, filt_s, acct_dropped, clean_s,
                     hash_s, dedup_s, f"{len(seen):,}", write_s, agg_s)
            chunk_start = time.monotonic()

        file_s = time.monotonic() - file_start
        done_s = time.monotonic() - run_start
        eta_all = (len(files) - i) * done_s / i
        log.info("File %s/%s done in %.0f s: %s — %s rows read, %s kept, %s "
                 "dropped (account filter + duplicates) | overall ETA ~%.0f min",
                 i, len(files), file_s, path.name, file_rows, file_kept,
                 file_rows - file_kept, eta_all / 60)

    log.info("RAW: %s rows written to %s (from %s files)",
             raw_rows, cfg.outputs[raw_key], len(files))

    # ------------------------------------------------ (27) grouping output
    grouping = pd.concat(grouping_parts, ignore_index=True).drop_duplicates()
    grouping.to_csv(cfg.outputs["grouping"], sep=CSV_SEP, index=False,
                    encoding=CSV_ENCODING, lineterminator="\r\n")
    log.info("Saved %s (%s rows)", cfg.outputs["grouping"], len(grouping))
    log.info("Brokerage per account: %s rows", brokerage_rows)

    # ------------------------------------------------ (30/31) mandates output
    mandates = (pd.concat(mandate_parts, ignore_index=True)
                .groupby(["Synthetic Grouping ID (latest)", "R_ID",
                          "ID 1 (Account)"] + MAND_COLS, as_index=False)["IA"]
                .sum())
    before = len(mandates)
    mandates = mandates[mandates["IA"] >= 1]
    log.info("Mandates: IA >= 1 filter: %s kept, %s dropped",
             len(mandates), before - len(mandates))
    mandates.to_csv(cfg.outputs["mandates"], sep=CSV_SEP, index=False,
                    encoding=CSV_ENCODING, lineterminator="\r\n")
    log.info("Saved %s (%s rows)", cfg.outputs["mandates"], len(mandates))

    # ------------------------------------------------ (10) consolidated sums
    consolidated = (pd.concat(consolidated_parts, ignore_index=True)
                    .groupby(HIER_COLS + ["Year (YYYY)"], as_index=False)
                    [MEASURE_COLS].sum())
    consolidated["File"] = "Consolidated"

    if filter_accounts:
        # comparing a filtered subset with the full-population check file
        # would always mismatch — the reconciliation lives in Quick Check
        log.info("Grouped check SKIPPED — account-filtered run (universe only). "
                 "Use the GMIS Quick Check workflow for reconciliation.")
        checks.append({"name": "Grouped check", "status": "ok",
                       "message": "Grouped check skipped (account-filtered run) "
                                  "— reconcile the full population with GMIS "
                                  "Quick Check"})
        summary = (consolidated.groupby("Year (YYYY)", as_index=False)
                   [MEASURE_COLS].sum().round(2))
        log.info("Universe totals per Year:\n%s",
                 summary.to_string(index=False, max_colwidth=30))
        log.info("=== DONE — OK ===")
        outputs = [str(cfg.outputs[raw_key]), str(cfg.outputs["grouping"]),
                   str(cfg.outputs["brokerage"]), str(cfg.outputs["mandates"])]
        return {"outputs": outputs, "checks": checks,
                "summary_title": "Universe totals per Year",
                "summary": summary.to_dict("records")}

    # ------------------------------------------------ (2/16/11/12) check side
    check_path = Path(cfg.inputs["grouped_check"])
    if not check_path.exists():
        raise FileNotFoundError(f"Missing grouped check file: {check_path}")
    chk = pd.read_csv(check_path, sep="\t", dtype=str, encoding="utf-8-sig",
                      keep_default_na=False, na_values=[""], quoting=3)
    rename = {col(chk, c): c for c in HIER_COLS + ["Year (YYYY)"] + MEASURE_COLS
              if col(chk, c) != c}
    chk = chk.rename(columns=rename)
    log.info("Loaded grouped check: %s rows (%s)", len(chk), check_path.name)
    for c in HIER_COLS + ["Year (YYYY)"]:
        chk[c] = chk[c].astype(str).str.strip()
    for c in MEASURE_COLS:
        chk[c] = pd.to_numeric(
            chk[c].astype(str).str.strip().str.replace(",", "", regex=False),
            errors="coerce")
    chk = chk.groupby(HIER_COLS + ["Year (YYYY)"], as_index=False)[MEASURE_COLS].sum()
    chk["File"] = "Check"

    # ------------------------------------------------ (13/14/15) compares
    union = pd.concat([consolidated, chk], ignore_index=True)
    compare1 = (union.groupby(["File", "Year (YYYY)"] + MAND_COLS + PROD_COLS
                              + REV_COLS, as_index=False)[MEASURE_COLS].sum())
    compare1.to_csv(cfg.outputs["compare1"], sep=CSV_SEP, index=False,
                    encoding=CSV_ENCODING, lineterminator="\r\n")
    log.info("Saved %s (%s rows)", cfg.outputs["compare1"], len(compare1))

    compare2 = union.groupby(["File", "Year (YYYY)"], as_index=False)[MEASURE_COLS].sum()
    compare2.to_csv(cfg.outputs["compare2"], sep=CSV_SEP, index=False,
                    encoding=CSV_ENCODING, lineterminator="\r\n")
    log.info("Saved %s (%s rows)", cfg.outputs["compare2"], len(compare2))

    # ------------------------------------------------ reconciliation check
    cons_t = (compare2[compare2["File"] == "Consolidated"]
              .set_index("Year (YYYY)")[MEASURE_COLS])
    chk_t = (compare2[compare2["File"] == "Check"]
             .set_index("Year (YYYY)")[MEASURE_COLS])
    diff = (cons_t.sub(chk_t, fill_value=0)).round(2)
    bad = diff.abs().gt(COMPARE_TOLERANCE)
    if bad.to_numpy().any():
        rows = []
        for year in diff.index:
            for m in MEASURE_COLS:
                if bad.loc[year, m]:
                    rows.append({"Year": year, "Measure": m,
                                 "Consolidated": round(cons_t.get(m, pd.Series())
                                                       .get(year, 0), 2),
                                 "Check": round(chk_t.get(m, pd.Series())
                                                .get(year, 0), 2),
                                 "Diff": diff.loc[year, m]})
        log.warning("Grouped check: %s Year x Measure cells differ by more "
                    "than %s", len(rows), COMPARE_TOLERANCE)
        checks.append({"name": "Grouped check", "status": "warning",
                       "message": f"Grouped check: {len(rows)} Year × Measure "
                                  f"totals differ from GWM Grouped for Check "
                                  f"(tolerance {COMPARE_TOLERANCE})",
                       "table": rows})
    else:
        log.info("Grouped check: all totals match GWM Grouped for Check "
                 "(tolerance %s)", COMPARE_TOLERANCE)
        checks.append({"name": "Grouped check", "status": "ok",
                       "message": "Grouped check: consolidated totals match "
                                  "GWM Grouped for Check — all measures OK"})

    # summary for the UI: totals per Year and File
    summary = compare2.copy()
    summary[MEASURE_COLS] = summary[MEASURE_COLS].round(2)
    log.info("Compare totals (File x Year):\n%s",
             summary.to_string(index=False, max_colwidth=30))
    log.info("=== DONE — OK ===")

    return {"outputs": [str(p) for k, p in cfg.outputs.items()
                        if k != "raw_universe"],
            "checks": checks,
            "summary_title": "Totals per File × Year (Compare2)",
            "summary": summary.to_dict("records")}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s")
    run({})
