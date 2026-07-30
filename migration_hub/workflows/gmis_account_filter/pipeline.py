"""
GMIS Account Filter — rewritten from the Alteryx filter workflow (filter.txt).

Builds the list of accounts we care about ("GMIS Account Universe"):

Relations (union 11 -> unique 13):
  * EVC coverage.xlsx        (20 -> 8)   column "Relation ID"
  * HEC_new.xlsm             (4 -> 9)    column "R_IDENTIFIER"
  * Relation_Structure       (18 -> 6)   GFIW_SECTOR_FLAG = "Y" -> RELATION_ID
  * 2655 Relation Replacement(25..29 -> 30)  financial thresholds:
      INVESTED_ASSET_MTD >= 49'000'000
      or NET_REVENUE_DECYTD_T_1 > 200'000
      or NET_REVENUE_YTD / month(PERIOD) * 12 > 200'000

Relations -> accounts (12/14/21/15): Accounts_Relation with
ACCOUNT_TYPE = "GMIS", join on RELATION_ID.

Accounts (union 16 -> unique 17/24):
  * accounts of the selected relations (15)
  * Masterlist <PERIOD>_Masterlist_UHNW_GFIW.xlsx (1 -> 23 -> 7):
      CONN_ID_TYPE# contains "GMIS" -> CONNECTOR_ID#
  * CUSTOM_PnL_Export.xlsx (33 -> 32): column PnL_id

Output: GMIS Account Universe.csv (one column "Account").
The join with the raw GMIS data (31 -> "0. GWM RAW GMIS Universe") is done
inside the GMIS workflow ("Filter to Account Universe" checkbox).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger("gmis_account_filter")

CSV_SEP = "|"
CSV_ENCODING = "utf-8-sig"

IA_THRESHOLD = 49_000_000
REVENUE_THRESHOLD = 200_000


class Config:
    def __init__(self, period: str):
        if not re.fullmatch(r"\d{6}", period):
            raise ValueError(f"Period must be in YYYYMM format, got: '{period}'")
        self.period = period
        self.year = period[:4]

        root = Path(os.environ.get("UBS_DATA_ROOT",
                                   r"\\Ubsprod.msad.ubs.net\groupshares"))
        gfo = root / "CHE" / "UHNWONEBANKC" / "GFO" / "GFO OneBank Analytics"
        procdes = gfo / "005_Projects" / "Process design" / "Database"

        self.inputs = {
            "masterlist_gfiw": (
                gfo / "001_Finance" / "007_Client Masterlist"
                / "003_Workflow tool upload" / self.year
                / f"{period}_Masterlist_UHNW_GFIW.xlsx"),
            "hec": root / "CHE" / "UHNWONEBANK" / "DASH" / "DaSh_TCC" / "HEC_new.xlsm",
            "evc_coverage": (gfo / "005_Projects" / "004_Python" / "GWM Dashboard"
                             / "Inputs for PowerBI" / "EVC coverage.xlsx"),
            "relation_structure": procdes / "Relation_Structure_CBD_Consolidated.csv",
            "accounts_relation": procdes / "Accounts_Relation_Consolidated.csv",
            "relation_replacement": (gfo / "005_Projects" / "004_Python"
                                     / "GMIS DATA" / "2655 Relation Replacement.csv"),
            "custom_pnl": (root / "CHE" / "GFIW_TRAVIS" / "BRO_OBR_SHARED"
                           / "ACCOUNTS_SCOPE" / "CUSTOM_PnL_Export.xlsx"),
        }

        out_env = os.environ.get("UBS_OUTPUT_DIR")
        out_db = Path(out_env) if out_env else (
            gfo / "005_Projects" / "004_Python" / "Alteryx" / "Database")
        self.outputs = {"universe": out_db / "GMIS Account Universe.csv"}

        try:
            from core import settings as hub_settings
            self.inputs = hub_settings.apply_input_overrides(
                "gmis_account_filter", self.inputs)
            self.outputs = hub_settings.apply_output_overrides(
                "gmis_account_filter", self.outputs)
        except ImportError:
            pass  # running standalone, outside the hub


def get_inputs(params: dict) -> dict:
    return dict(Config(str(params.get("period", "")).strip()).inputs)


def get_outputs(params: dict) -> dict:
    return dict(Config(str(params.get("period", "")).strip()).outputs)


# ---------------------------------------------------------------- helpers
def col(df: pd.DataFrame, name: str) -> str:
    def norm(s: str) -> str:
        return " ".join(str(s).replace("\xa0", " ").split()).lower()
    for c in df.columns:
        if norm(c) == norm(name):
            return c
    raise KeyError(f"Missing column '{name}'. Available: {list(df.columns)}")


def uniq(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().replace("", pd.NA).dropna().drop_duplicates()


# ---------------------------------------------------------------- main
def run(params: dict, progress=print) -> dict:
    cfg = Config(str(params.get("period", "")).strip())
    log.info("=== GMIS Account Filter, period %s ===", cfg.period)

    # ------------------------------------------------- relations branch
    evc = pd.read_excel(cfg.inputs["evc_coverage"], sheet_name="EVC coverage",
                        dtype=str)
    rel_evc = uniq(evc[col(evc, "Relation ID")])
    log.info("EVC coverage: %s rows -> %s unique relations", len(evc), len(rel_evc))

    hec = pd.read_excel(cfg.inputs["hec"], dtype=str)
    rel_hec = uniq(hec[col(hec, "R_IDENTIFIER")])
    log.info("HEC: %s rows -> %s unique relations", len(hec), len(rel_hec))

    rs = pd.read_csv(cfg.inputs["relation_structure"], sep="|", dtype=str,
                     encoding="latin-1", keep_default_na=False, na_values=[""])
    before = len(rs)
    rs = rs[rs[col(rs, "GFIW_SECTOR_FLAG")] == "Y"]
    log.info("Relation Structure: GFIW_SECTOR_FLAG=Y filter: %s kept, %s dropped",
             len(rs), before - len(rs))
    rel_rs = uniq(rs[col(rs, "RELATION_ID")])

    rr = pd.read_csv(cfg.inputs["relation_replacement"], sep=",", dtype=str,
                     encoding="latin-1", keep_default_na=False, na_values=[""])
    ia = pd.to_numeric(rr[col(rr, "INVESTED_ASSET_MTD")], errors="coerce")
    rev_ytd = pd.to_numeric(rr[col(rr, "NET_REVENUE_YTD")], errors="coerce")
    rev_prev = pd.to_numeric(rr[col(rr, "NET_REVENUE_DECYTD_T_1")], errors="coerce")
    counter = pd.to_numeric(rr[col(rr, "PERIOD")].astype(str).str[-2:],
                            errors="coerce")
    keep = (ia >= IA_THRESHOLD) | (rev_prev > REVENUE_THRESHOLD) \
        | (rev_ytd / counter * 12 > REVENUE_THRESHOLD)
    log.info("2655 Relation Replacement: thresholds filter: %s kept, %s dropped",
             int(keep.sum()), int((~keep.fillna(False)).sum()))
    rel_rr = uniq(rr.loc[keep.fillna(False), col(rr, "R_IDENTIFIER")])

    relations = (pd.concat([rel_evc, rel_hec, rel_rs, rel_rr])
                 .drop_duplicates().rename("Relation"))
    log.info("Relations union: %s unique relations "
             "(EVC %s, HEC %s, Relation Structure %s, 2655 %s)",
             len(relations), len(rel_evc), len(rel_hec), len(rel_rs), len(rel_rr))

    # ------------------------------------- relations -> GMIS accounts
    acc = pd.read_csv(cfg.inputs["accounts_relation"], sep="|", dtype=str,
                      encoding="latin-1", keep_default_na=False, na_values=[""])
    before = len(acc)
    acc = acc[acc[col(acc, "ACCOUNT_TYPE")] == "GMIS"]
    log.info("Accounts_Relation: ACCOUNT_TYPE=GMIS filter: %s kept, %s dropped",
             len(acc), before - len(acc))
    pairs = acc[[col(acc, "RELATION_ID"), col(acc, "ACCOUNT_ID")]].drop_duplicates()
    pairs.columns = ["RELATION_ID", "ACCOUNT_ID"]

    matched_mask = relations.isin(set(pairs["RELATION_ID"]))
    joined = pairs[pairs["RELATION_ID"].isin(set(relations))]
    log.info("Relations -> accounts join: %s relations matched, %s unmatched "
             "(no GMIS account) -> %s account rows",
             int(matched_mask.sum()), int((~matched_mask).sum()), len(joined))
    acc_from_rel = uniq(joined["ACCOUNT_ID"])

    # ------------------------------------------------- accounts branch
    ml = pd.read_excel(cfg.inputs["masterlist_gfiw"], dtype=str)
    before = len(ml)
    ml = ml[ml[col(ml, "CONN_ID_TYPE#")].astype(str).str.contains("GMIS", na=False)]
    log.info("Masterlist %s: CONN_ID_TYPE# contains GMIS: %s kept, %s dropped",
             cfg.period, len(ml), before - len(ml))
    acc_ml = uniq(ml[col(ml, "CONNECTOR_ID#")])

    pnl = pd.read_excel(cfg.inputs["custom_pnl"], sheet_name="Sheet1", dtype=str)
    acc_pnl = uniq(pnl[col(pnl, "PnL_id")])
    log.info("Custom PnL export: %s rows -> %s unique accounts", len(pnl), len(acc_pnl))

    universe = (pd.concat([acc_from_rel, acc_ml, acc_pnl])
                .drop_duplicates().rename("Account").sort_values())
    log.info("Account Universe: %s unique accounts "
             "(from relations %s, Masterlist %s, Custom PnL %s)",
             len(universe), len(acc_from_rel), len(acc_ml), len(acc_pnl))

    out = Path(cfg.outputs["universe"])
    out.parent.mkdir(parents=True, exist_ok=True)
    universe.to_frame().to_csv(out, sep=CSV_SEP, index=False,
                               encoding=CSV_ENCODING, lineterminator="\r\n")
    log.info("Saved %s (%s accounts)", out, len(universe))
    log.info("=== DONE — OK ===")

    summary = [
        {"Source": "EVC coverage (relations)", "Unique IDs": len(rel_evc)},
        {"Source": "HEC (relations)", "Unique IDs": len(rel_hec)},
        {"Source": "Relation Structure GFIW=Y (relations)", "Unique IDs": len(rel_rs)},
        {"Source": "2655 thresholds (relations)", "Unique IDs": len(rel_rr)},
        {"Source": "-> accounts via Accounts_Relation", "Unique IDs": len(acc_from_rel)},
        {"Source": f"Masterlist {cfg.period} (accounts)", "Unique IDs": len(acc_ml)},
        {"Source": "Custom PnL (accounts)", "Unique IDs": len(acc_pnl)},
        {"Source": "TOTAL Account Universe", "Unique IDs": len(universe)},
    ]
    return {"outputs": [str(out)],
            "checks": [{"name": "Universe", "status": "ok",
                        "message": f"Account Universe built: {len(universe):,} "
                                   f"unique accounts"}],
            "summary_title": "Account Universe by source",
            "summary": summary}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s")
    run({"period": os.environ.get("UBS_REPORTING_PERIOD", "")})
