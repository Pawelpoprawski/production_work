"""
Hub settings — central, editable path configuration persisted to settings.yaml.

Structure of settings.yaml:
  global:
    data_root: <path>      # overrides the UBS share root (env UBS_DATA_ROOT)
    output_dir: <path>     # overrides the default output dir (env UBS_OUTPUT_DIR)
  workflows:
    <workflow_key>:
      inputs:
        <input_name>: <full path override>
      outputs:
        <output_name>: <full path override>

Pipelines call `apply_input_overrides(...)` / `apply_output_overrides(...)`
after building their default paths, so everything stays editable in one place
(the Settings page) without touching individual workflow configs. Only paths
that differ from the defaults are stored.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

HUB_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = HUB_DIR / "settings.yaml"


def load() -> dict:
    if SETTINGS_FILE.exists():
        return yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
    return {}


def save(data: dict) -> None:
    SETTINGS_FILE.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def apply_global_env() -> None:
    """Push global data_root/output_dir into the env vars pipelines read."""
    g = load().get("global") or {}
    if g.get("data_root"):
        os.environ["UBS_DATA_ROOT"] = str(g["data_root"])
    if g.get("output_dir"):
        os.environ["UBS_OUTPUT_DIR"] = str(g["output_dir"])


def workflow_overrides(wf_key: str) -> dict:
    return (load().get("workflows") or {}).get(wf_key) or {}


def apply_input_overrides(wf_key: str, inputs: dict) -> dict:
    """Replace default input paths with per-workflow overrides from settings.

    Works with values that are plain paths or tuples whose first element
    is the path (the rest — sheet name, separator, encoding — is kept).
    """
    overrides = workflow_overrides(wf_key).get("inputs") or {}
    out = {}
    for name, value in inputs.items():
        new_path = overrides.get(name)
        if new_path:
            if isinstance(value, tuple):
                value = (Path(new_path),) + value[1:]
            else:
                value = Path(new_path)
        out[name] = value
    return out


def apply_output_overrides(wf_key: str, outputs: dict) -> dict:
    """Replace default output paths with per-workflow overrides from settings."""
    overrides = workflow_overrides(wf_key).get("outputs") or {}
    return {name: Path(overrides[name]) if overrides.get(name) else path
            for name, path in outputs.items()}


def set_global(data_root: str, output_dir: str) -> None:
    data = load()
    data["global"] = {"data_root": data_root.strip() or None,
                      "output_dir": output_dir.strip() or None}
    save(data)
    apply_global_env()


def set_workflow_paths(wf_key: str, inputs: dict[str, str],
                       outputs: dict[str, str]) -> None:
    """Persist per-workflow path overrides; pass only paths that differ
    from the defaults — an empty dict removes all overrides."""
    data = load()
    wfs = data.setdefault("workflows", {})
    entry = {}
    if inputs:
        entry["inputs"] = inputs
    if outputs:
        entry["outputs"] = outputs
    if entry:
        wfs[wf_key] = entry
    else:
        wfs.pop(wf_key, None)
    save(data)
