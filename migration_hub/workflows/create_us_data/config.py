"""
GWM Dashboard US pipeline configuration (rewritten from Alteryx).

The only parameter changed each month: REPORTING_PERIOD (YYYYMM format).
All input/output paths are derived from it automatically.

For local testing set the UBS_DATA_ROOT environment variable to a directory
with sample data (same subdirectory structure).
"""
import os
from pathlib import Path

# ------------------------------------------------------------------ parameters
REPORTING_PERIOD = os.environ.get("UBS_REPORTING_PERIOD", "202606")  # YYYYMM

YEAR = REPORTING_PERIOD[:4]          # "2026"
MONTH = REPORTING_PERIOD[4:6]        # "06"
PREV_YEAR = str(int(YEAR) - 1)       # "2025"
# Alteryx filter: [Year] = "2025" or [Year] = "2026"
YEARS_KEPT = [PREV_YEAR, YEAR]

# ------------------------------------------------------------------ directories
_DEFAULT_ROOT = r"\\Ubsprod.msad.ubs.net\groupshares"
DATA_ROOT = Path(os.environ.get("UBS_DATA_ROOT", _DEFAULT_ROOT))

_US_DATA = DATA_ROOT / "GLOBAL" / "USG_Evolution_Analytics_Global"
_RAS = _US_DATA / "DATA_US_RAS" / "Full Population"
_GFO = DATA_ROOT / "CHE" / "UHNWONEBANKC" / "GFO" / "GFO OneBank Analytics"
_EXCH = DATA_ROOT / "CHE" / "CL UHNW-GFO" / "CL EXCHANGE GFO"

# ------------------------------------------------------------------ inputs
INPUTS = {
    # (path, sheet name or None for CSV)
    "strategic_clients": (
        _US_DATA / "DATA_US" / "Data delivery from Eric" / REPORTING_PERIOD
        / f"Final_Strategic_Clients_Analysis File_{REPORTING_PERIOD}.xlsx",
        "FIRST_FILE",
    ),
    "prod_mapping": (
        _GFO / "005_Projects" / "004_Python" / "GWM Dashboard"
        / "Inputs for Alteryx" / "US" / "PROD mapping.xlsx",
        "PROD mapping",
    ),
    "manual_adjustments": (
        _GFO / "001_Finance" / "001_Performance Report"
        / "001_GFO Manual adjustments" / YEAR / f"{YEAR}_{MONTH}"
        / f"{YEAR}_{MONTH}_GFO manual adjustments.xlsx",
        "ADJUSTMENT RAW DATA",
    ),
    "bp_source": (
        _EXCH / YEAR / "GFIW 1UBS Data sourcing" / "Prod" / "01 - Source files"
        / "BP Source Data.xlsx",
        "GB Revs",
    ),
    "gfiw_metadata": (
        _EXCH / "2025" / "GFIW 1UBS Data sourcing" / "Prod" / "01 - Source files"
        / "GFIW Metadata.xlsx",
        "BP Products",
    ),
    "masterlist": (
        _GFO / "001_Finance" / "007_Client Masterlist" / "003_Workflow tool upload"
        / YEAR / f"{REPORTING_PERIOD}_Masterlist.xlsx",
        "Sheet1",
    ),
    "uhnw_us": (
        _GFO / "005_Projects" / "Process design" / "Database" / "UHNW US.xlsx",
        "Post ADW July + Engaged",
    ),
    "dig_us": (
        _GFO / "005_Projects" / "Process design" / "Database" / "DIG US.xlsx",
        "Sheet1",
    ),
    # pipe-delimited CSV
    "accounts_relation": (
        _GFO / "005_Projects" / "Process design" / "Database"
        / "Accounts_Relation_Consolidated.csv", None),
    "relation_structure": (
        _GFO / "005_Projects" / "Process design" / "Database"
        / "Relation_Structure_CBD_Consolidated.csv", None),
    "ras_nnl": (_RAS / "UHNW_NNL.csv", None),
    "ras_nnfga": (_RAS / "UHNW_NNFGA.csv", None),
    "ras_nnm": (_RAS / "UHNW_NNM.csv", None),
    "ras_revenue": (_RAS / "UHNW_Revenue.csv", None),
    "ras_loan_bal": (_RAS / "UHNW_LOAN_BAL.csv", None),
    "ras_depo": (_RAS / "UHNW_DEPO.csv", None),
    "ras_ia": (_RAS / "UHNW_IA.csv", None),
}

# ------------------------------------------------------------------ outputs
OUTPUT_DIR = Path(os.environ.get(
    "UBS_OUTPUT_DIR",
    str(_GFO / "005_Projects" / "004_Python" / "Database csv"),
))
OUT_FIN_CSV = OUTPUT_DIR / "df_monthly_us_fin.csv"
OUT_QV_CSV = OUTPUT_DIR / "df_monthly_us_qv.csv"
OUT_CONTROL_CSV = OUTPUT_DIR / f"control_check_{REPORTING_PERIOD}.csv"

LOG_DIR = Path(os.environ.get("UBS_LOG_DIR", str(Path(__file__).parent / "logs")))

CSV_SEP = "|"
CSV_ENCODING = "utf-8-sig"

# FGA mandate ID list (Text Input from Alteryx, tool 551)
FGA_MANDATE_IDS = [
    "40264", "40266", "40267", "40269", "40272", "40317", "40449", "40450",
    "40451", "40774", "40775", "40268", "40308", "40316", "40851", "40884",
    "40816", "41295", "40815", "41294", "40809", "40810", "40811", "40812",
    "40813", "40817", "41055", "41298", "40844", "40849", "40850", "40852",
    "41328", "41329", "40818", "41188", "41278", "41279", "41293", "40299",
    "40301", "40305", "40306", "40730", "40800", "40801", "40300", "40302",
    "40631", "40802", "40803", "41326", "40505", "40506", "41299", "41327",
    "40819", "40820", "40821", "40867", "41247", "41325", "41324",
]
