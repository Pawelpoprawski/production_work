"""
Migration Hub — cockpit for workflows migrated from Alteryx to Python.

Run:  streamlit run app.py
Adding a workflow: a new directory in workflows/ with meta.yaml + pipeline.py.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import registry
from core import settings as hub_settings
from core.runner import read_history
from core.ui import WIDE, logs_section, settings_page, workflow_page

st.set_page_config(page_title="Production Cockpit", page_icon="🚀", layout="wide")

hub_settings.apply_global_env()  # global data_root/output_dir from settings.yaml

WORKFLOWS = registry.discover()


# ================================================================ home page
def home() -> None:
    st.title("🚀 PRODUCTION COCKPIT")
    st.caption("Alteryx workflows rewritten in Python / Streamlit")

    ready = sum(w.is_ready for w in WORKFLOWS)
    c1, c2, c3 = st.columns(3)
    c1.metric("Workflows", len(WORKFLOWS))
    c2.metric("Migrated", ready)
    c3.metric("To migrate", len(WORKFLOWS) - ready)

    search = st.text_input("🔍 Search workflows", "")

    last_by_wf = {h["workflow"]: h for h in reversed(read_history(limit=1000))}

    rows = []
    for w in WORKFLOWS:
        if search and search.lower() not in (w.name + w.description).lower():
            continue
        last = last_by_wf.get(w.key)
        rows.append({
            "Workflow": w.name,
            "Category": w.category,
            "Last run": last["started"] if last else "—",
            "Last period": (last.get("params") or {}).get("period", "—") if last else "—",
            "Run time [min]": (round(last["duration_s"] / 60, 1)
                               if last and last.get("duration_s") is not None else None),
            "Result": ("✅" if last.get("status") == "ok" else "❌") if last else "—",
            "Description": w.description.splitlines()[0] if w.description else "",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, **WIDE)

    st.markdown("Pick a workflow from the menu on the left to see its "
                "instructions and start a run.")


def all_logs() -> None:
    st.title("📜 Hub logs")
    st.caption("All run logs, newest first")
    logs_section(prefix=None, key_ns="hub")


# ================================================================ navigation
# sidebar sections by the `category` field from meta.yaml (first-seen order)
sections: dict[str, list] = {"": [st.Page(home, title="Home",
                                          icon="🏠", default=True),
                                  st.Page(all_logs, title="Logs",
                                          icon="📜", url_path="logs"),
                                  st.Page(lambda: settings_page(WORKFLOWS),
                                          title="Settings", icon="⚙️",
                                          url_path="settings")]}
for wf in WORKFLOWS:
    icon = "🟢" if wf.is_ready else ("🟡" if wf.status == "in_progress" else "⚪")
    sections.setdefault(wf.category, []).append(
        st.Page(lambda wf=wf: workflow_page(wf),
                title=wf.name, icon=icon, url_path=wf.key))

st.navigation(sections).run()
