"""
GMIS Quick Check — thin wrapper page around the quick-total-check mode of
the GMIS workflow (step 1 of the chain: Quick Check -> Account Filter ->
GMIS full load). All logic lives in workflows/gmis/pipeline.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GMIS_DIR = Path(__file__).resolve().parent.parent / "gmis"


def _gmis():
    """Load workflows/gmis/pipeline.py under its own module name."""
    name = "workflows.gmis.pipeline"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _GMIS_DIR / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def get_inputs(params: dict) -> dict:
    gmis = _gmis()
    inputs = dict(gmis.Config().inputs)
    inputs.pop("account_universe", None)  # not used by the quick check
    return inputs


def get_outputs(params: dict) -> dict:
    gmis = _gmis()
    out = Path(gmis.Config().outputs["compare2"]).with_name("Quick total check.csv")
    return {"quick_check": out}


def run(params: dict, progress=print) -> dict:
    gmis = _gmis()
    return gmis.run({"quick_check": True}, progress=progress)
