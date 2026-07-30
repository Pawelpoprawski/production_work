"""
Client Flags — rewritten from the Alteryx flagging workflow.

Branches (all keyed to Relation_ID via 2655 Account Replacement):
  * DAC:      DAC Clients file (11 Direct-Access flag columns) + GMIS DAC
              mandates -> flag_DAC_FLAG
  * PMA CH:   H1_PMA_CH document statuses cross-tabbed per CDOK ->
              PMS / CMS / RS / PMA_STATUS / PCS / schedule flags
  * PMA APAC: H1 - PMA_Mandates_APAC (Bc 4005/4006) ->
              PMA Capital Market Schedule flag

Output: table_flags_<year>.csv (pipe-delimited, CRLF, UTF-8 BOM).

Hub contract: run(params, progress) -> {"outputs": [...]},
get_inputs(params) -> {name: path}.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger("client_flags")

CSV_SEP = "|"
CSV_ENCODING = "utf-8-sig"

# the 11 Direct-Access flag columns from the DAC Clients file (Formula 3)
DAC_FLAG_COLUMNS = [
    "204 Direct Access Suitability FX Flag",
    "205 Direct Access Suitability Equity Flag",
    "206 Direct Access Suitability Fixed Income Flag",
    "219 FIM end client FX (DAC) Flag",
    "220 FIM end client Equity (DAC) Flag",
    "221 FIM end client Fixed Income (DAC) Flag",
    "222 Direct Access Agreement WM Client to UBS IB Flag",
    "223 Direct Access Agreement FIM Client to UBS IB Flag",
    "231 FI Direct Access Client /Fixed Income Flag",
    "237 Other Direct Access Client Flag",
    "256 Direct Access Client to UBS IB ETD Flag",
]

# PMA CH document filter (Filter 234)
PMA_CDOK = ["66675", "66676", "66677", "66678", "66669", "66661", "66668"]
PMA_STATUSES = ["10", "20", "34"]

# mandate -> coverage cluster mapping (Text Input 2743); only DAC rows matter
DAC_MANDATE_IDS = [
    "41101", "41104", "41211", "41212", "41213", "41221", "41275",
]


class Config:
    """All paths derived from the YYYYMM period (env overrides for local tests)."""

    def __init__(self, period: str):
        if not re.fullmatch(r"\d{6}", period):
            raise ValueError(f"Period must be in YYYYMM format, got: '{period}'")
        self.period = period
        self.year = period[:4]

        root = Path(os.environ.get("UBS_DATA_ROOT",
                                   r"\\Ubsprod.msad.ubs.net\groupshares"))
        prep = root / "CHE" / "UHNWONEBANK" / "DATA_PREPARATION"
        gfo = root / "CHE" / "UHNWONEBANKC" / "GFO" / "GFO OneBank Analytics"
        flagging = prep / "Flagging" / period
        pma_dir = flagging / "H1 - Onboarding Solutions - PMA Capital Market Schedule"

        self.inputs = {
            "account_replacement": (
                prep / "Relation Structuring" / period / "05 - Base Downloads"
                / "CCAR" / "2655 Account Replacement.csv", ",", "latin-1"),
            "dac_clients": (
                flagging / "04 - IB Access" / "DAC Clients - 12554532.csv",
                ",", "latin-1"),
            "pma_ch": (pma_dir / "H1_PMA_CH.csv", ",", "latin-1"),
            # note: the file name really has TWO spaces after "H1 -" on the share
            "pma_apac": (pma_dir / "H1 -  PMA_Mandates_APAC.csv", ",", "utf-8-sig"),
            "gmis_mandates": (
                gfo / "005_Projects" / "004_Python" / "Alteryx" / "Database"
                / "temps" / "GMIS Mandates per account.csv", "|", "utf-8-sig"),
        }

        out_dir = Path(os.environ.get(
            "UBS_OUTPUT_DIR",
            str(gfo / "005_Projects" / "Process design" / "Database")))

        self.outputs = {"table_flags": out_dir / f"table_flags_{self.year}.csv"}

        # central overrides from the hub Settings page (settings.yaml)
        try:
            from core import settings as hub_settings
            self.inputs = hub_settings.apply_input_overrides("client_flags", self.inputs)
            self.outputs = hub_settings.apply_output_overrides("client_flags", self.outputs)
        except ImportError:
            pass  # running standalone, outside the hub

        self.output = self.outputs["table_flags"]


def get_inputs(params: dict) -> dict:
    cfg = Config(str(params.get("period", "")).strip())
    return {key: path for key, (path, _sep, _enc) in cfg.inputs.items()}


def get_outputs(params: dict) -> dict:
    return Config(str(params.get("period", "")).strip()).outputs


# ============================================================== helpers
def col(df: pd.DataFrame, name: str) -> str:
    """Find a column case-insensitively (matches Alteryx behaviour)."""
    for c in df.columns:
        if c.strip().lower() == name.strip().lower():
            return c
    raise KeyError(f"Missing column '{name}'. Available: {list(df.columns)}")


def read_input(cfg: Config, key: str) -> pd.DataFrame:
    path, sep, enc = cfg.inputs[key]
    if not path.exists():
        raise FileNotFoundError(f"[{key}] missing input file: {path}")
    df = pd.read_csv(path, sep=sep, dtype=str, encoding=enc,
                     keep_default_na=False, na_values=[""])
    df.columns = [c.strip() for c in df.columns]
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].str.strip()
    log.info("Loaded [%s]: %s rows, %s columns (%s)", key, len(df), len(df.columns), path.name)
    return df


# ============================================================== branches
def load_base(cfg: Config) -> pd.DataFrame:
    """2655 Account Replacement -> Relation_ID + PARTNER_ID_1 (Select 2551)."""
    df = read_input(cfg, "account_replacement")
    base = pd.DataFrame({
        "Relation_ID": df[col(df, "R_IDENTIFIER")],
        "PARTNER_ID_1": df[col(df, "PARTNER_ID_1")],
    })
    log.info("Base: %s partner/relation rows", len(base))
    return base


def build_dac(cfg: Config, base: pd.DataFrame) -> pd.DataFrame:
    """DAC Clients file + GMIS DAC mandates -> Relation_ID, Flag_DAC_FLAG."""
    # --- file branch (nodes 975/976/3/2/4)
    dac = read_input(cfg, "dac_clients")
    flags = pd.DataFrame({c: pd.to_numeric(dac[col(dac, c)], errors="coerce")
                          for c in DAC_FLAG_COLUMNS if _has_col(dac, c)})
    dac["DAC_FLAG"] = (flags == 1).any(axis=1).map({True: "Y", False: "N"})
    matched_mask = dac[col(dac, "Global Partner ID 1")].isin(set(base["PARTNER_ID_1"]))
    joined = dac.merge(base, left_on=col(dac, "Global Partner ID 1"),
                       right_on="PARTNER_ID_1", how="inner")
    log.info("DAC: base join on Global Partner ID 1: %s matched, %s unmatched (dropped)",
             int(matched_mask.sum()), int((~matched_mask).sum()))
    fit = (joined.groupby("Relation_ID", as_index=False)["DAC_FLAG"].max()
                 .rename(columns={"DAC_FLAG": "FIT_ISIN_DAC"}))
    log.info("DAC file branch: %s relations", len(fit))

    # --- GMIS mandate branch (nodes 2736..2742): DAC coverage-cluster mandates
    gmis = read_input(cfg, "gmis_mandates")
    before = len(gmis)
    gmis = gmis[gmis[col(gmis, "Mand. Type ID")].isin(DAC_MANDATE_IDS)].copy()
    log.info("DAC: GMIS mandate-type filter: %s kept, %s dropped", len(gmis), before - len(gmis))
    gmis["Mandate_DAC"] = "Y"
    matched_mask = gmis[col(gmis, "ID 1 (Account)")].isin(set(base["PARTNER_ID_1"]))
    gjoin = base.merge(gmis[[col(gmis, "ID 1 (Account)"), "Mandate_DAC"]],
                       left_on="PARTNER_ID_1", right_on=col(gmis, "ID 1 (Account)"),
                       how="inner")
    log.info("DAC: GMIS base join on ID 1 (Account): %s matched, %s unmatched (dropped)",
             int(matched_mask.sum()), int((~matched_mask).sum()))
    mand = gjoin.groupby("Relation_ID", as_index=False)["Mandate_DAC"].max()
    log.info("DAC mandate branch: %s relations", len(mand))

    # --- union + Formula 17 + Summarize 20
    both = pd.concat([fit, mand], ignore_index=True)
    both["Flag_DAC_FLAG"] = ((both.get("FIT_ISIN_DAC") == "Y")
                             | (both.get("Mandate_DAC") == "Y")).map({True: "Y", False: "N"})
    out = both.groupby("Relation_ID", as_index=False)["Flag_DAC_FLAG"].max()
    log.info("DAC: %s relations with a DAC verdict", len(out))
    return out


def _has_col(df: pd.DataFrame, name: str) -> bool:
    try:
        col(df, name)
        return True
    except KeyError:
        return False


def build_pma_ch(cfg: Config, base: pd.DataFrame) -> pd.DataFrame:
    """H1_PMA_CH documents -> PMS/CMS/RS/PMA_STATUS + PCS/schedule flags."""
    ch = read_input(cfg, "pma_ch")
    ch["Partner_ID_1"] = "3032-" + ch[col(ch, "Business Relation")]
    matched_mask = ch["Partner_ID_1"].isin(set(base["PARTNER_ID_1"]))
    ch = ch.merge(base, left_on="Partner_ID_1", right_on="PARTNER_ID_1", how="inner")
    log.info("PMA CH: base join on Partner_ID_1: %s matched, %s unmatched (dropped)",
             int(matched_mask.sum()), int((~matched_mask).sum()))

    cdok = col(ch, "CDOK")
    status = col(ch, "Status")
    before = len(ch)
    ch = ch[ch[cdok].isin(PMA_CDOK) & ch[status].isin(PMA_STATUSES)]
    log.info("PMA CH: CDOK/status filter: %s kept, %s dropped", len(ch), before - len(ch))

    # dedup + cross tab: one column per CDOK, documents concatenated (nodes 242/236)
    dedup = ch[["Relation_ID", cdok, col(ch, "Document")]].drop_duplicates()
    dedup.columns = ["Relation_ID", "CDOK", "Document"]
    wide = (dedup.pivot_table(index="Relation_ID", columns="CDOK", values="Document",
                              aggfunc=lambda s: ",".join(s.astype(str)))
                 .reset_index())
    for c in PMA_CDOK:  # missing CDOK columns behave as empty
        if c not in wide.columns:
            wide[c] = pd.NA

    def has(c):  # !IsEmpty([CDOK])
        return wide[c].notna() & (wide[c].astype(str) != "")

    # Formula 235
    wide["PMS"] = pd.Series("", index=wide.index).mask(has("66669"), "candidate").mask(has("66677"), "signed")
    wide["CMS"] = pd.Series("", index=wide.index).mask(has("66676"), "signed")
    wide["RS"] = pd.Series("", index=wide.index).mask(has("66678"), "signed")
    wide["PMA_STATUS"] = (
        pd.Series("-----", index=wide.index).mask(has("66669"), "PMS_C").mask(has("66677"), "PMS_A")
        + "/" + pd.Series("-----", index=wide.index).mask(has("66676"), "CMS_A")
        + "/" + pd.Series("-----", index=wide.index).mask(has("66678"), "RS_A"))
    na = pd.Series([None] * len(wide), index=wide.index, dtype=object)
    wide["flag_PCS"] = na.mask(has("66677"), "Y")
    wide["flag_PCS_CANDIDATE"] = na.mask(
        has("66669") & (wide["flag_PCS"].fillna("") != "Y"), "Y")
    wide["Onboarding Solutions - PMA Capital Market Schedule"] = na.mask(has("66676"), "Y")
    wide["Onboarding Solutions - PMA Private Market Schedule"] = na.mask(has("66677"), "Y")
    wide["Onboarding Solutions - PMA Research Schedule"] = na.mask(has("66678"), "Y")

    keep = ["Relation_ID", "PMS", "CMS", "RS", "PMA_STATUS",
            "flag_PCS", "flag_PCS_CANDIDATE",
            "Onboarding Solutions - PMA Capital Market Schedule",
            "Onboarding Solutions - PMA Private Market Schedule",
            "Onboarding Solutions - PMA Research Schedule"]
    out = wide[keep].fillna("").groupby("Relation_ID", as_index=False).max()
    log.info("PMA CH: %s relations", len(out))
    return out


def build_pma_apac(cfg: Config) -> pd.DataFrame:
    """H1 - PMA_Mandates_APAC -> PMA Capital Market Schedule = Y (Bc 4005/4006)."""
    ap = read_input(cfg, "pma_apac")
    before = len(ap)
    ap = ap[ap[col(ap, "Bc")].isin(["4005", "4006"])].copy()
    log.info("PMA APAC: Bc 4005/4006 filter: %s kept, %s dropped", len(ap), before - len(ap))
    ap["Onboarding Solutions - PMA Capital Market Schedule"] = "Y"
    out = (ap.groupby(col(ap, "R Identifier"), as_index=False)
             ["Onboarding Solutions - PMA Capital Market Schedule"].max())
    out.columns = ["Relation_ID", "Onboarding Solutions - PMA Capital Market Schedule"]
    log.info("PMA APAC: %s relations", len(out))
    return out


# ============================================================== main
def run(params: dict, progress=print) -> dict:
    cfg = Config(str(params.get("period", "")).strip())
    log.info("=== Client Flags, period %s ===", cfg.period)

    base = load_base(cfg)
    branches = [build_dac(cfg, base), build_pma_ch(cfg, base), build_pma_apac(cfg)]

    # Union 2718 (by name) + Summarize 2728: max() per Relation_ID, flag_ prefix
    union = pd.concat(branches, ignore_index=True)
    agg_map = {
        "PMS": "flag_PMS", "CMS": "flag_CMS", "RS": "flag_RS",
        "PMA_STATUS": "flag_PMA_STATUS", "Flag_DAC_FLAG": "flag_DAC_FLAG",
        "flag_PCS": "flag_PCS", "flag_PCS_CANDIDATE": "flag_PCS_CANDIDATE",
        "Onboarding Solutions - PMA Capital Market Schedule":
            "flag_Onboarding Solutions - PMA Capital Market Schedule",
        "Onboarding Solutions - PMA Private Market Schedule":
            "flag_Onboarding Solutions - PMA Private Market Schedule",
        "Onboarding Solutions - PMA Research Schedule":
            "flag_Onboarding Solutions - PMA Research Schedule",
    }
    # fill NaN with "" so max() works on object columns (Alteryx max skips nulls;
    # "" sorts lowest, so any real value wins) — restored to empty at the end
    src_cols = [c for c in agg_map if c in union.columns]
    union[src_cols] = union[src_cols].fillna("")
    flags = (union.groupby("Relation_ID", as_index=False)
                  .agg({src: "max" for src in src_cols})
                  .rename(columns=agg_map)
                  .rename(columns={"Relation_ID": "flag_Relation_ID"}))

    # final column order (Select 2720)
    order = ["flag_Relation_ID", "flag_PCS", "flag_PCS_CANDIDATE",
             "flag_Onboarding Solutions - PMA Capital Market Schedule",
             "flag_Onboarding Solutions - PMA Private Market Schedule",
             "flag_Onboarding Solutions - PMA Research Schedule",
             "flag_CMS", "flag_DAC_FLAG", "flag_PMS", "flag_RS", "flag_PMA_STATUS"]
    flags = flags[[c for c in order if c in flags.columns]]

    cfg.output.parent.mkdir(parents=True, exist_ok=True)
    flags.to_csv(cfg.output, sep=CSV_SEP, index=False,
                 encoding=CSV_ENCODING, lineterminator="\r\n")
    log.info("Saved %s (%s rows)", cfg.output, len(flags))
    log.info("=== DONE — OK ===")
    return {"outputs": [str(cfg.output)]}
