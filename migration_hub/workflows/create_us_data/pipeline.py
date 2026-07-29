"""
Create US Data — hub wrapper around the existing GWM Dashboard US pipeline
(gwm_pipeline.py + config.py, rewritten 1:1 from Alteryx).

Hub contract: run(params: dict, progress) -> dict with an "outputs" key,
optional get_inputs(params) -> dict[name, path] for the Inputs table.
"""
from __future__ import annotations

import importlib
import os
import re


def _load_config(period: str):
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError(f"Period must be in YYYYMM format, got: '{period}'")
    os.environ["UBS_REPORTING_PERIOD"] = period
    import config as cfg
    importlib.reload(cfg)

    # central overrides from the hub Settings page (settings.yaml)
    try:
        from core import settings as hub_settings
        cfg.INPUTS = hub_settings.apply_input_overrides("create_us_data", cfg.INPUTS)
        outs = hub_settings.apply_output_overrides("create_us_data", {
            "fin": cfg.OUT_FIN_CSV, "qv": cfg.OUT_QV_CSV,
            "control": cfg.OUT_CONTROL_CSV,
        })
        cfg.OUT_FIN_CSV, cfg.OUT_QV_CSV, cfg.OUT_CONTROL_CSV = \
            outs["fin"], outs["qv"], outs["control"]
        cfg.OUTPUT_DIR = outs["fin"].parent
    except ImportError:
        pass  # running standalone, outside the hub
    return cfg


def get_inputs(params: dict) -> dict:
    """Input files for the given period (feeds the Inputs table in the UI)."""
    cfg = _load_config(str(params.get("period", "")).strip())
    return {key: path for key, (path, _sheet) in cfg.INPUTS.items()}


def get_outputs(params: dict) -> dict:
    """Output files for the given period (feeds the Settings paths table)."""
    cfg = _load_config(str(params.get("period", "")).strip())
    return {"fin": cfg.OUT_FIN_CSV, "qv": cfg.OUT_QV_CSV,
            "control": cfg.OUT_CONTROL_CSV}


def run(params: dict, progress=print) -> dict:
    cfg = _load_config(str(params.get("period", "")).strip())
    import gwm_pipeline
    importlib.reload(gwm_pipeline)

    # logging is configured by the hub runner — disable the local setup
    gwm_pipeline.setup_logging = lambda: None
    gwm_pipeline.main()

    return {"outputs": [str(cfg.OUT_FIN_CSV), str(cfg.OUT_QV_CSV),
                        str(cfg.OUT_CONTROL_CSV)]}
