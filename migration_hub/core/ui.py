"""
Shared UI components — one page template used by every workflow,
generated from meta.yaml. Written once, works for all workflows.

Every workflow page has the same layout, in this order:
  title + status badge, description, "📖 Instructions" expander (markdown),
  compact Parameters row, "📥 Inputs" table (auto-checked), Start button
  with live log, downloadable outputs, run history, "📜 Logs" browser.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from . import settings as hub_settings
from .registry import Workflow
from .runner import LOG_DIR, read_history, run_workflow

STATUS_BADGE = {
    "ready": "🟢 ready",
    "in_progress": "🟡 in migration",
    "planned": "⚪ planned",
}


def _period_options(p: dict) -> tuple[list[str], int]:
    """YYYYMM periods around the 'current' period (today minus offset_days).

    Default: today − 10 days determines the base period, with ±3 periods
    to choose from. Returns (options newest first, index of the base period).
    """
    offset = int(p.get("offset_days", 10))
    span = int(p.get("span", 3))
    base = pd.Period(datetime.now() - timedelta(days=offset), freq="M")
    options = [(base + i).strftime("%Y%m") for i in range(span, -span - 1, -1)]
    return options, span  # newest first, base period in the middle


def _param_widget(p: dict):
    """Build a widget from a parameter definition in meta.yaml."""
    kind = p.get("type", "text")
    label = p.get("label", p["key"])
    default = p.get("default", "")
    help_ = p.get("help")
    if kind == "period":
        options, base_idx = _period_options(p)
        return st.selectbox(label, options, index=base_idx, help=help_)
    if kind == "select":
        return st.selectbox(label, p.get("options", []), help=help_)
    if kind == "checkbox":
        return st.checkbox(label, value=bool(default), help=help_)
    return st.text_input(label, value=str(default), help=help_)


def logs_section(prefix: str | None = None, key_ns: str = "") -> None:
    """Log browser: file list from logs/, preview, refresh, download.

    prefix — show only logs of a given workflow (file name starts with its key);
    None — all hub logs.
    """
    st.subheader("📜 Logs")
    files = sorted(LOG_DIR.glob(f"{prefix or ''}*.log"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        st.caption("No logs yet.")
        return

    c1, c2 = st.columns([4, 1])
    with c1:
        chosen = st.selectbox(
            "Log file", files,
            format_func=lambda p: f"{p.name}  ({p.stat().st_size / 1024:.1f} kB)",
            key=f"log_select_{key_ns}")
    with c2:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh", key=f"log_refresh_{key_ns}"):
            st.rerun()

    text = chosen.read_text(encoding="utf-8", errors="replace")
    tail = st.slider("Last lines", 50, 2000, 200, step=50,
                     key=f"log_tail_{key_ns}")
    lines = text.splitlines()
    st.code("\n".join(lines[-tail:]), language="log")
    st.download_button("⬇ Download full log", data=text.encode("utf-8"),
                       file_name=chosen.name, key=f"log_dl_{key_ns}")


def settings_page(workflows: list[Workflow]) -> None:
    """Central path configuration: global roots + per-workflow input overrides."""
    st.title("⚙️ Settings")
    st.caption("All paths in one place — overrides are saved to settings.yaml "
               "and applied the next time a workflow builds its paths.")
    data = hub_settings.load()

    # ---------------- global roots
    st.subheader("Global")
    g = data.get("global") or {}
    c1, c2 = st.columns(2)
    with c1:
        data_root = st.text_input(
            "Data root (UBS_DATA_ROOT)", value=g.get("data_root") or "",
            placeholder=r"\\Ubsprod.msad.ubs.net\groupshares",
            help="Root of the shared drive. Leave empty for the default. "
                 "Point it at a local sample-data directory for testing.")
    with c2:
        output_dir = st.text_input(
            "Output directory (UBS_OUTPUT_DIR)", value=g.get("output_dir") or "",
            help="Default output directory for all workflows. Empty = per-workflow default.")
    if st.button("💾 Save global", type="primary"):
        hub_settings.set_global(data_root, output_dir)
        st.success("Saved. Global roots apply to newly built paths.")

    # -------- per-workflow paths (dynamic from get_inputs / get_outputs):
    # editable Path column prefilled with the current value; only paths that
    # differ from the default are stored as overrides
    st.subheader("Workflow paths")
    st.caption("Edit the Path column and click Save. A path equal to the "
               "workflow default is not stored — edit it back (or clear the "
               "override in settings.yaml) to return to the default.")
    default_period = _period_options({})[0][_period_options({})[1]]

    def _paths_editor(title: str, defaults: dict, overrides: dict, key: str) -> dict:
        """Render an editable Name|Path table; return {name: path} diffs."""
        st.markdown(f"**{title}**")
        table = pd.DataFrame([
            {"Name": name, "Path": overrides.get(name, str(path))}
            for name, path in defaults.items()])
        edited = st.data_editor(table, hide_index=True, use_container_width=True,
                                disabled=["Name"], key=key)
        return {name: path.strip()
                for name, path in zip(edited["Name"], edited["Path"])
                if path.strip() and path.strip() != str(defaults[name])}

    for wf in workflows:
        if not wf.is_ready:
            continue
        try:
            module = wf.load_pipeline()
            get_inputs = getattr(module, "get_inputs", None)
            get_outputs = getattr(module, "get_outputs", None)
            params = {"period": default_period}
            in_defaults = get_inputs(params) if get_inputs else {}
            out_defaults = get_outputs(params) if get_outputs else {}
        except Exception as e:
            st.warning(f"{wf.name}: failed to load paths ({e})")
            continue
        if not in_defaults and not out_defaults:
            continue

        overrides = hub_settings.workflow_overrides(wf.key)
        n_ov = len(overrides.get("inputs") or {}) + len(overrides.get("outputs") or {})
        label = f"📂 {wf.name} — {len(in_defaults)} inputs, {len(out_defaults)} outputs"
        if n_ov:
            label += f" · {n_ov} overridden"
        with st.expander(label, expanded=False):
            in_diff = _paths_editor("Inputs", in_defaults,
                                    overrides.get("inputs") or {},
                                    f"in_{wf.key}") if in_defaults else {}
            out_diff = _paths_editor("Outputs", out_defaults,
                                     overrides.get("outputs") or {},
                                     f"out_{wf.key}") if out_defaults else {}
            if st.button("💾 Save", key=f"save_{wf.key}"):
                hub_settings.set_workflow_paths(wf.key, in_diff, out_diff)
                st.success("Saved.")
                st.rerun()


def _inputs_section(wf: Workflow, params: dict) -> bool:
    """Input files table: exists? + last modified date.

    A workflow pipeline may expose get_inputs(params) -> dict[name, path].
    The check runs automatically on page render; the "Check inputs" button
    forces a re-read (e.g. when a file has just been uploaded to the share).
    Returns True when all inputs exist (or no inputs are defined).
    """
    st.subheader("📥 Inputs")
    try:
        module = wf.load_pipeline()
        get_inputs = getattr(module, "get_inputs", None)
    except Exception as e:
        st.warning(f"Failed to load the pipeline: {e}")
        return True
    if get_inputs is None:
        st.caption("This workflow does not define its input list yet "
                   "(no `get_inputs` function in pipeline.py).")
        return True

    if st.button("🔍 Check inputs", key=f"chk_{wf.key}"):
        st.rerun()

    try:
        inputs = get_inputs(params)
    except Exception as e:
        st.error(f"Error while building the input list: {e}")
        return False

    rows, all_ok = [], True
    for name, path in inputs.items():
        path = Path(path)
        exists = path.exists()
        all_ok &= exists
        rows.append({
            "Status": "✅ OK" if exists else "❌ MISSING",
            "Input": name,
            "Last modified": (
                pd.Timestamp(path.stat().st_mtime, unit="s")
                .strftime("%Y-%m-%d %H:%M") if exists else "—"),
            "File": path.name,
            "Path": str(path.parent),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if not all_ok:
        missing = sum(r["Status"].startswith("❌") for r in rows)
        st.warning(f"{missing} of {len(rows)} input files are missing.")
    return all_ok


def workflow_page(wf: Workflow) -> None:
    """Single workflow page: instructions, parameters, Start, log, outputs."""
    st.title(wf.name)
    st.caption(f"{STATUS_BADGE.get(wf.status, wf.status)}"
               + (f" · owner: {wf.owner}" if wf.owner else ""))
    if wf.description:
        st.markdown(wf.description)

    if wf.instructions:
        with st.expander("📖 Instructions", expanded=False):
            st.markdown(wf.instructions)

    if not wf.is_ready:
        st.info("This workflow has not been migrated from Alteryx yet. "
                "Status: **" + STATUS_BADGE.get(wf.status, wf.status) + "**")
        return

    # ---------------- parameters (compact: narrow columns, small header)
    st.markdown("**Parameters**")
    params = {}
    cols = st.columns(4)
    for i, p in enumerate(wf.params):
        with cols[i % 4]:
            params[p["key"]] = _param_widget(p)

    # ---------------- inputs (checked automatically on every render)
    inputs_ok = _inputs_section(wf, params)

    # ---------------- run
    if not inputs_ok:
        st.caption("⚠️ You can still start the run, but the pipeline will "
                   "most likely fail on the missing file.")
    if st.button("▶ Start", type="primary"):
        log_box = st.status("Running…", expanded=True)

        def progress(msg: str) -> None:
            log_box.write(msg)

        record = run_workflow(wf, params, progress=progress)

        if record["status"] == "ok":
            log_box.update(label=f"Finished in {record['duration_s']} s", state="complete")
            result = record.get("result") or {}
            outputs = result.get("outputs", [])
            if outputs:
                st.subheader("Outputs")
                for out in outputs:
                    out = Path(out)
                    if out.exists():
                        st.download_button(f"⬇ {out.name}", data=out.read_bytes(),
                                           file_name=out.name, key=str(out))
        else:
            log_box.update(label="Error", state="error")
            st.error("The workflow failed — details in the log below.")
            st.code(record.get("error", ""), language="text")

    # ---------------- history
    history = read_history(wf.key, limit=10)
    if history:
        st.subheader("Recent runs")
        st.dataframe(pd.DataFrame([
            {"Date": h["started"], "Status": "✅" if h["status"] == "ok" else "❌",
             "Duration [s]": h.get("duration_s"), "Parameters": str(h.get("params"))}
            for h in history
        ]), hide_index=True, use_container_width=True)

    logs_section(prefix=wf.key, key_ns=wf.key)
