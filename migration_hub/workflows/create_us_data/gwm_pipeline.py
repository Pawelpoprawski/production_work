"""
GWM Dashboard US — monthly pipeline (rewritten from the Alteryx workflow).

Sources:
  * RAS Full Population (CSV, pipe): NNL, NNFGA, NNM/NNA, Revenue, Loans,
    Deposits, IA (FGA + Invested Assets)  -> "ADW" branch
  * GFO Manual adjustments (xlsx)         -> "Manual Adjustments" branch
  * Banker Portal / BP Source Data (xlsx) -> "Banker Portal" branch
  * DIG US (xlsx)                         -> "DIG" branch
  * Strategic Clients (xlsx)              -> QV file (US client list)

Outputs (pipe-delimited CSV, as in Alteryx; .yxdb outputs skipped):
  * df_monthly_us_fin.csv
  * df_monthly_us_qv.csv
  * control_check_<PERIOD>.csv (equivalent of the control CrossTab)

Run:  python gwm_pipeline.py
Month parameter: config.REPORTING_PERIOD or env UBS_REPORTING_PERIOD.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

import pandas as pd

import config as cfg

log = logging.getLogger("gwm_us")

try:
    from core.export import write_csv_batched
except ImportError:  # standalone run outside the hub
    def write_csv_batched(df, path, *, sep=",", encoding="utf-8",
                          lineterminator="\r\n", index=False,
                          label=None, logger=None):
        df.to_csv(path, sep=sep, index=index, encoding=encoding,
                  lineterminator=lineterminator)
        (logger or log).info("Saved %s (%s rows)", path, len(df))

# checks collected during the run, shown by the hub UI (green/warning)
CHECKS: list[dict] = []


# ============================================================== helpers
def setup_logging() -> None:
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = cfg.LOG_DIR / f"pipeline_{cfg.REPORTING_PERIOD}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(logfile, encoding="utf-8")],
    )
    log.info("Log: %s", logfile)


def col(df: pd.DataFrame, name: str) -> str:
    """Find a column case-insensitively (matches Alteryx behaviour)."""
    for c in df.columns:
        if c.strip().lower() == name.strip().lower():
            return c
    raise KeyError(f"Missing column '{name}'. Available: {list(df.columns)}")


def read_input(key: str) -> pd.DataFrame:
    path, sheet = cfg.INPUTS[key]
    if not path.exists():
        raise FileNotFoundError(f"[{key}] missing input file: {path}")
    if sheet is None:
        df = pd.read_csv(path, sep=cfg.CSV_SEP, dtype=str, encoding="utf-8-sig",
                         keep_default_na=False, na_values=[""])
    else:
        df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    log.info("Loaded [%s]: %s rows, %s columns (%s)", key, len(df), len(df.columns), path.name)
    return df


def cleanse(df: pd.DataFrame) -> pd.DataFrame:
    """Equivalent of the Data Cleanse macro: trim whitespace in text columns."""
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].str.strip()
    return df


def to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def std_period(s: pd.Series) -> pd.Series:
    """Alteryx: replace(left([Period],7),'-','') -> 'YYYYMM'."""
    return s.astype(str).str[:7].str.replace("-", "", regex=False)


def add_common(df: pd.DataFrame, measure: str | None = None) -> pd.DataFrame:
    """Common formulas: Period=YYYYMM, HH='HH'+HH, Year=left(Period,4)."""
    if measure is not None:
        df["Measure"] = measure
    df["Period"] = std_period(df["Period"])
    df["HH"] = "HH" + df["HH"].astype(str)
    df["Year"] = df["Period"].str[:4]
    return df


# ============================================================== ADW branch (RAS)
def ras_nnl() -> pd.DataFrame:
    df = cleanse(read_input("ras_nnl"))
    out = pd.DataFrame({
        "HH": df[col(df, "ACC_MHH_N")],
        "Period": df[col(df, "YR_MNTH")],
        "Value": to_num(df[col(df, "NN_LOAN_MAND_MTD_Y")]),
    })
    return add_common(out, "Net New Loans")


def ras_nnfga() -> pd.DataFrame:
    df = cleanse(read_input("ras_nnfga"))
    out = pd.DataFrame({
        "HH": df[col(df, "ACC_MHH_N")],
        "Period": df[col(df, "YR_MNTH")],
        "Prod. Line Name": df[col(df, "REC_TYP_C")],
        "Value": to_num(df[col(df, "NN_LOAN_MAND_MTD_Y")]),
    })
    out = add_common(out, "Net New Fee Generating Assets")
    out["Prod. Type Name"] = out["Prod. Line Name"]
    return out


def ras_nnm_nna() -> list[pd.DataFrame]:
    """NNM and NN Assets: YTD values converted to monthly (diff within HH+Year group)."""
    df = cleanse(read_input("ras_nnm"))
    base = pd.DataFrame({
        "HH": df[col(df, "ACC_MHH_N")],
        "Period": df[col(df, "NNA_AS_OF_D")],
        "Value": to_num(df[col(df, "ACC_NNA_TOT_Y")]),
    })
    frames = []
    for measure in ("Net New Money", "NN Assets"):
        m = add_common(base.copy(), measure)
        m = m.sort_values(["HH", "Year", "Period"])
        # Alteryx Multi-Row: [Value]-[Row-1:Value], first row of a group -> null
        m["Value"] = m.groupby(["HH", "Year"])["Value"].diff()
        frames.append(m)
    return frames


def ras_revenue() -> pd.DataFrame:
    df = cleanse(read_input("ras_revenue"))
    out = pd.DataFrame({
        "HH": df[col(df, "ACC_MHH_N")],
        "Period": df[col(df, "REPORTING_MONTH")],
        "Prod. Line ID": df[col(df, "LEVEL_3_CODE")],
        "Prod. Line Name": df[col(df, "LEVEL_3_DESCRIPTION")],
        "Prod. Type ID": df[col(df, "LEVEL_4_CODE")],
        "Prod. Type Name": df[col(df, "LEVEL_4_DESCRIPTION")],
        "Value": to_num(df[col(df, "REVENUE")]),
    })
    out["Measure"] = (out["Prod. Line Name"].fillna("") + out["Prod. Type Name"].fillna(""))
    out.loc[out["Measure"] == "", "Measure"] = "OTHEROTHER"
    out = add_common(out)
    return out


def ras_simple(key: str, period_col: str, value_col: str, measure: str) -> pd.DataFrame:
    df = cleanse(read_input(key))
    out = pd.DataFrame({
        "HH": df[col(df, "ACC_MHH_N")],
        "Period": df[col(df, period_col)],
        "Value": to_num(df[col(df, value_col)]),
    })
    return add_common(out, measure)


def ras_ia_measures() -> list[pd.DataFrame]:
    """UHNW_IA.csv -> Invested Assets (all rows) + Fee Generating Assets (filtered)."""
    df = cleanse(read_input("ras_ia"))
    base = pd.DataFrame({
        "HH": df[col(df, "ACC_MHH_N")],
        "Period": df[col(df, "REPORTING_MONTH")],
        "Value": to_num(df[col(df, "IA")]),
        "GPC_MAND_C": df[col(df, "GPC_MAND_C")],
        "GPCE_DPNT_C": df[col(df, "GPCE_DPNT_C")],
    })
    invested = add_common(base[["HH", "Period", "Value"]].copy(), "Invested Assets")

    # FGA: mandates != 40359 pass through; 40359 only when GPCE_DPNT_C is on the list
    not_special = base[base["GPC_MAND_C"] != "40359"]
    special_ok = base[(base["GPC_MAND_C"] == "40359")
                      & base["GPCE_DPNT_C"].isin(cfg.FGA_MANDATE_IDS)]
    log.info("FGA: %s standard rows + %s from mandate 40359 (allowed GPCE)",
             len(not_special), len(special_ok))
    fga = pd.concat([not_special, special_ok], ignore_index=True)
    fga = add_common(fga[["HH", "Period", "Value"]].copy(), "Fee Generating Assets")
    return [invested, fga]


def build_adw_branch(us_client_ids: pd.Series) -> pd.DataFrame:
    frames = [ras_nnl(), ras_nnfga(), *ras_nnm_nna(),
              ras_simple("ras_loan_bal", "YR_MNTH", "LOAN_B", "Total Loans"),
              ras_simple("ras_depo", "REPORTING_MONTH", "DEPO_SUM", "Total Deposits"),
              *ras_ia_measures(), ras_revenue()]
    measures = pd.concat(frames, ignore_index=True)
    log.info("ADW: %s rows after union of all measures", len(measures))

    # PROD mapping: Key -> GFIW lvl 1-4 (first per Key)
    pm = cleanse(read_input("prod_mapping"))
    pm = pm.groupby(col(pm, "Key"), as_index=False).first()
    pm = pm.rename(columns={col(pm, "Key"): "Key"})
    gfiw_cols = [col(pm, f"GFIW lvl {i}") for i in range(1, 5)]
    pm = pm[["Key"] + gfiw_cols]
    pm.columns = ["Key", "GFIW lvl 1", "GFIW lvl 2", "GFIW lvl 3", "GFIW lvl 4"]

    # join Measure=Key (case-insensitive for robustness; unmapped rows are logged)
    measures["_k"] = measures["Measure"].str.upper().str.strip()
    pm["_k"] = pm["Key"].str.upper().str.strip()
    joined = measures.merge(pm.drop(columns=["Key"]), on="_k", how="left")
    unmapped = joined[joined["GFIW lvl 1"].isna()]
    log.info("ADW: PROD mapping join: %s matched, %s unmatched",
             len(joined) - len(unmapped), len(unmapped))
    if len(unmapped):
        log.warning("ADW: %s rows with unmapped Measure (examples: %s) — dropped as in Alteryx",
                    len(unmapped), sorted(unmapped["Measure"].unique())[:10])
        missing = (unmapped.groupby("Measure").agg(
                       Rows=("Measure", "size"), Value_sum=("Value", "sum"))
                   .round(2).reset_index())
        CHECKS.append({
            "name": "PROD mapping", "status": "warning",
            "message": f"PROD mapping: {len(unmapped)} rows dropped — "
                       f"{len(missing)} Measure(s) missing from PROD mapping.xlsx",
            "table": missing.to_dict("records"),
        })
    else:
        CHECKS.append({
            "name": "PROD mapping", "status": "ok",
            "message": f"PROD mapping: all {len(joined)} rows matched — no missing mappings",
        })
    adw = joined[joined["GFIW lvl 1"].notna()].drop(columns=["_k"]).copy()
    adw = adw.rename(columns={"HH": "Relation_ID"})

    # constants (Formula 486)
    adw["CLIENT_ID"] = adw["Relation_ID"]
    adw["Source"] = "ADW"
    adw["Master Account"] = adw["Relation_ID"]
    adw["Sub Account"] = adw["Relation_ID"]
    adw["Master Account Name"] = "WM US HH"
    adw["Sub Account Name"] = "WM US HH"
    adw["Region"] = "Americas"

    # semi-join with the US client list (join 534)
    before = len(adw)
    adw = adw[adw["Relation_ID"].isin(set(us_client_ids))]
    log.info("ADW: US client semi-join: %s matched, %s unmatched (dropped)",
             len(adw), before - len(adw))
    return adw


# ============================================================== QV branch (US)
def build_qv() -> tuple[pd.DataFrame, pd.Series]:
    df = read_input("strategic_clients")
    hh = "HH" + df[col(df, "HOUSEHOLD_ID")].astype(str)
    grp = pd.DataFrame({
        "Client ID": hh,
        "CA": df[col(df, "FA_Name")],
        "CC Level 4": df[col(df, "BRANCH_NAME")],
        "CC Level 2": df[col(df, "DIVISION_NAME")],
        "CC Level 3": df[col(df, "REGION_NAME")],
        "wealth": to_num(df[col(df, "wealth")]),
    })
    qv = (grp.groupby(["Client ID", "CA", "CC Level 4", "CC Level 2", "CC Level 3"],
                      as_index=False, dropna=False)["wealth"].sum()
             .rename(columns={"wealth": "Wealth"}))
    qv["Is Active Client"] = "Y"
    qv["BC"] = "US"
    qv["Relation"] = qv["Client ID"]
    qv["Partner Type"] = "Client"
    qv["Client Description"] = "WM only"
    qv["ClientSource"] = "US"
    qv["Relation_ID"] = qv["Client ID"]
    qv["Client Name/ID"] = qv["Relation_ID"]
    qv.insert(0, "Timestamp", datetime.now().strftime("%Y-%m-%d"))
    log.info("QV: %s US clients", len(qv))
    return qv, qv["Client ID"].drop_duplicates()


# ==================================================== Manual Adjustments branch
def build_manual_adjustments() -> pd.DataFrame:
    df = cleanse(read_input("manual_adjustments"))
    before = len(df)
    df = df[df[col(df, "ACCOUNT_IDENT")].str.contains("HH", na=False)]
    log.info("Manual Adjustments: HH filter: %s kept, %s dropped", len(df), before - len(df))
    out = pd.DataFrame({
        "Year": df[col(df, "YEAR")],
        "Period": df[col(df, "PERIOD")],
        "Relation_ID": df[col(df, "ACCOUNT_IDENT")].str.replace("TEC_", "", regex=False),
        "Value": to_num(df[col(df, "AMOUNT_USD")]).round(2),
        "Comment": df[col(df, "Comment")],
        "GFIW lvl 1": df[col(df, "GFIW lvl 1")],
        "GFIW lvl 2": df[col(df, "GFIW lvl 2")],
        "GFIW lvl 3": df[col(df, "GFIW lvl 3")],
        "GFIW lvl 4": df[col(df, "GFIW lvl 4")],
    })
    out["CLIENT_ID"] = out["Relation_ID"]
    out["Source"] = "Manual Adjustments"
    out["Master Account"] = out["Relation_ID"]
    out["Sub Account"] = out["Relation_ID"]
    out["Master Account Name"] = "Technical Account"
    out["Sub Account Name"] = "Technical Account"
    log.info("Manual Adjustments: %s rows", len(out))
    return out


# ======================================================= Banker Portal branch
def build_banker_portal(masterlist_uhnw: pd.DataFrame, masterlist_ids: pd.Series) -> pd.DataFrame:
    bp = cleanse(read_input("bp_source"))
    rev = col(bp, "Gross Revenue (Post RMP - USD)")
    before = len(bp)
    bp = bp[to_num(bp[rev]).fillna(0) != 0].copy()
    log.info("Banker Portal: zero-revenue filter: %s kept, %s dropped", len(bp), before - len(bp))

    bp["Key"] = bp[col(bp, "One Bank Product")].fillna("") + bp[col(bp, "Dashboard Product")].fillna("")
    bp["Sub Account"] = bp[col(bp, "Parent CMIS ID")]
    bp["Sub Account Name"] = bp[col(bp, "Client Name")]
    bp["Source"] = "Banker Portal"
    bp["Comment"] = "GB for UHNW+"
    bp = bp.rename(columns={
        col(bp, "YearMonth"): "Period",
        col(bp, "Client Name"): "Master Account Name",
        rev: "Value",
        col(bp, "Global Region"): "Region",
    })
    bp["Value"] = to_num(bp["Value"])

    # BP Products metadata: Key -> GFIW lvl1..4
    meta = cleanse(read_input("gfiw_metadata"))
    meta = meta.rename(columns={col(meta, "BP Product key"): "Key"})
    keep = ["Key"] + [col(meta, f"GFIW lvl{i}") for i in range(1, 5)]
    meta = meta[keep].drop_duplicates(subset="Key")
    before = len(bp)
    bp = bp.merge(meta, on="Key", how="inner")
    log.info("Banker Portal: GFIW metadata join on Key: %s matched, %s unmatched (dropped)",
             len(bp), before - len(bp))
    bp = bp.rename(columns={col(bp, "Parent CMIS ID"): "Master Account"}).drop(columns=["Key"])

    # only clients from the UHNW US masterlist (join 572)
    conn = col(masterlist_uhnw, "CONNECTOR_ID#")
    before = len(bp)
    bp = bp[bp["Master Account"].isin(set(masterlist_uhnw[conn]))]
    log.info("Banker Portal: UHNW masterlist semi-join: %s matched, %s unmatched (dropped)",
             len(bp), before - len(bp))

    # accounts outside the monthly masterlist -> Relation_ID from Accounts_Relation (join 570)
    acc = cleanse(read_input("accounts_relation"))
    acc_id, rel_id = col(acc, "ACCOUNT_ID"), col(acc, "RELATION_ID")
    acc_outside = acc[~acc[acc_id].isin(set(masterlist_ids))]
    before = len(bp)
    bp = bp.merge(acc_outside[[acc_id, rel_id]].drop_duplicates(subset=acc_id),
                  left_on="Master Account", right_on=acc_id, how="inner")
    log.info("Banker Portal: Accounts_Relation join: %s matched, %s unmatched "
             "(dropped rows go to a control Browse in Alteryx)", len(bp), before - len(bp))
    bp = bp.rename(columns={rel_id: "Relation_ID"}).drop(columns=[acc_id])
    bp = bp.rename(columns={col(bp, "GFIW lvl1"): "GFIW lvl 1",
                            col(bp, "GFIW lvl2"): "GFIW lvl 2",
                            col(bp, "GFIW lvl3"): "GFIW lvl 3",
                            col(bp, "GFIW lvl4"): "GFIW lvl 4"})
    bp["CLIENT_ID"] = bp["Relation_ID"]
    bp["Period"] = std_period(bp["Period"])
    if "Year" not in bp.columns:
        bp["Year"] = bp["Period"].str[:4]
    keep_cols = ["Relation_ID", "CLIENT_ID", "Period", "Year", "Value", "Source", "Comment",
                 "Region", "Master Account", "Master Account Name", "Sub Account",
                 "Sub Account Name", "GFIW lvl 1", "GFIW lvl 2", "GFIW lvl 3", "GFIW lvl 4"]
    return bp[keep_cols]


# ================================================================= DIG branch
def build_dig(masterlist_uhnw_all: pd.DataFrame) -> pd.DataFrame:
    dig = cleanse(read_input("dig_us"))
    ml = masterlist_uhnw_all
    ml_client = col(ml, "CLIENT_ID")

    before = len(dig)
    dig = dig[dig[col(dig, "CLIENT_ID")].isin(set(ml[ml_client]))].copy()
    log.info("DIG: UHNW masterlist semi-join on CLIENT_ID: %s matched, %s unmatched (dropped)",
             len(dig), before - len(dig))
    date_closed = dig[col(dig, "Date Closed")].astype(str)
    dig["Year"] = date_closed.str[:4]
    dig["Period"] = dig["Year"] + date_closed.str[5:7]
    dig = pd.DataFrame({
        "Opportunity Code": dig[col(dig, "Opportunity Code")],
        "Investor Name": dig[col(dig, "Investor Name")],
        "Value": to_num(dig[col(dig, "UBS Gross Fee (US$)")]),
        "CLIENT_ID": dig[col(dig, "CLIENT_ID")],
        "Year": dig["Year"],
        "Period": dig["Period"],
    }).drop_duplicates()

    rs = cleanse(read_input("relation_structure"))
    rs_rel = col(rs, "RELATION_ID")
    rs_ids = set(rs[rs_rel])

    matched = dig[dig["CLIENT_ID"].isin(rs_ids)].copy()
    matched["Relation_ID"] = matched["CLIENT_ID"]

    # unmatched -> via HOUSEHOLD_ID from the masterlist
    unmatched = dig[~dig["CLIENT_ID"].isin(rs_ids)].copy()
    log.info("DIG: Relation Structure direct match: %s matched, %s unmatched (retry via household)",
             len(matched), len(unmatched))
    if len(unmatched):
        hh_map = ml[[ml_client, col(ml, "HOUSEHOLD_ID")]].copy()
        hh_map.columns = ["CLIENT_ID", "HOUSEHOLD_ID"]
        before = len(unmatched)
        unmatched = unmatched.merge(hh_map, on="CLIENT_ID", how="inner")
        log.info("DIG: household lookup in masterlist: %s matched, %s unmatched (dropped)",
                 len(unmatched), before - len(unmatched))
        unmatched["HOUSEHOLD_ID"] = "HH" + unmatched["HOUSEHOLD_ID"].astype(str)
        before = len(unmatched)
        unmatched = unmatched[unmatched["HOUSEHOLD_ID"].isin(rs_ids)]
        log.info("DIG: household match to Relation Structure: %s matched, %s unmatched (dropped)",
                 len(unmatched), before - len(unmatched))
        unmatched = (unmatched.groupby(["CLIENT_ID", "Year", "Period", "Value",
                                        "Opportunity Code", "Investor Name"], as_index=False)
                              .agg(Relation_ID=("HOUSEHOLD_ID", "first")))
        # Alteryx Summarize 589: collapse clients of the same household to one row
        before = len(unmatched)
        unmatched = unmatched.drop(columns=["CLIENT_ID"]).drop_duplicates(
            subset=["Relation_ID", "Year", "Period", "Value",
                    "Opportunity Code", "Investor Name"])
        log.info("DIG: household dedup (Summarize 589): %s -> %s rows", before, len(unmatched))
        unmatched["CLIENT_ID"] = unmatched["Relation_ID"]
    dig_all = pd.concat([matched, unmatched], ignore_index=True).drop(columns=["Investor Name"])
    log.info("DIG: %s rows (matched %s, via household %s)",
             len(dig_all), len(matched), len(dig_all) - len(matched))

    pos = dig_all.copy()
    pos["GFIW lvl 1"] = "GWM Gross Revenue"
    pos["GFIW lvl 2"] = "Other Income"
    pos["GFIW lvl 3"] = "Other Income"
    pos["GFIW lvl 4"] = "DIG"
    neg = dig_all.copy()
    neg["GFIW lvl 1"] = "GWM Gross Revenue"
    neg["GFIW lvl 2"] = "Other Income"
    neg["GFIW lvl 3"] = "Other Income"
    neg["GFIW lvl 4"] = "Other Income"
    neg["Value"] = -neg["Value"]

    out = pd.concat([pos, neg], ignore_index=True)
    out["Master Account"] = "Technical account"
    out["Master Account Name"] = "Technical account"
    out["Sub Account"] = "Technical account"
    out["Sub Account Name"] = "Technical account"
    out["Source"] = "DIG"
    out["Comment"] = "DIG Adjustment"
    out["Region"] = "Americas"
    return out


# ==================================================================== main
def main() -> dict:
    CHECKS.clear()
    setup_logging()
    log.info("=== GWM Dashboard US, period %s ===", cfg.REPORTING_PERIOD)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # QV (US client list) — also needed as a filter for ADW
    qv, us_client_ids = build_qv()
    write_csv_batched(qv, cfg.OUT_QV_CSV, sep=cfg.CSV_SEP,
                      encoding=cfg.CSV_ENCODING, logger=log)

    # masterlists used by BP and DIG
    ml_uhnw_all = cleanse(read_input("uhnw_us"))
    ml_uhnw = ml_uhnw_all[ml_uhnw_all[col(ml_uhnw_all, "MASTER_LIST")]
                          .str.contains("UHNW", na=False)]
    ml_month = cleanse(read_input("masterlist"))
    ml_month_ids = ml_month[col(ml_month, "CONNECTOR_ID#")].drop_duplicates()

    branches = [
        build_adw_branch(us_client_ids),
        build_manual_adjustments(),
        build_banker_portal(ml_uhnw, ml_month_ids),
        # DIG joins use the raw UHNW US.xlsx (tool 571), not the UHNW-filtered list
        build_dig(ml_uhnw_all),
    ]
    fin = pd.concat(branches, ignore_index=True)
    log.info("FIN: %s rows after branch union", len(fin))

    before = len(fin)
    fin = fin[fin["Year"].astype(str).isin(cfg.YEARS_KEPT)]
    log.info("FIN: Year filter in %s: %s -> %s rows", cfg.YEARS_KEPT, before, len(fin))

    fin.insert(0, "Timestamp", datetime.now().strftime("%Y-%m-%d"))
    write_csv_batched(fin, cfg.OUT_FIN_CSV, sep=cfg.CSV_SEP,
                      encoding=cfg.CSV_ENCODING, logger=log)

    # control: Value sums by GFIW lvl 1/2 x Period (the Alteryx CrossTab)
    control = (fin.groupby(["GFIW lvl 1", "GFIW lvl 2", "Period"])["Value"]
                  .sum().unstack("Period").reset_index())
    write_csv_batched(control, cfg.OUT_CONTROL_CSV, sep=cfg.CSV_SEP,
                      encoding=cfg.CSV_ENCODING, logger=log)

    log.info("Value summary by Source:\n%s",
             fin.groupby("Source")["Value"].agg(["count", "sum"]).to_string())

    # final control view: Value by GFIW lvl 2 x Period (columns ascending)
    summary = (fin.groupby(["GFIW lvl 2", "Period"])["Value"].sum()
                  .unstack("Period").sort_index(axis=1).round(2).reset_index())
    log.info("Summary by GFIW lvl 2 x Period:\n%s", summary.to_string(index=False))
    log.info("=== DONE — OK ===")
    return {"checks": list(CHECKS),
            "summary_title": "Value by GFIW lvl 2 × Period",
            "summary": summary.to_dict("records")}


if __name__ == "__main__":
    main()
