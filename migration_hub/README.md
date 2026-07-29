# Migration Hub

Cockpit for workflows migrated from Alteryx to Python / Streamlit.

## Run

```
pip install -r requirements.txt
streamlit run app.py
```

The home page shows a catalog of all workflows with migration status
and recent runs. Every workflow gets its own page (left-hand menu) with
instructions, parameters, a Start button, a live log and output downloads.

## Structure

```
migration_hub/
├── app.py            # home page + navigation (generated automatically)
├── core/
│   ├── registry.py   # workflow discovery from workflows/*/meta.yaml
│   ├── runner.py     # run execution, logs, history (logs/history.jsonl)
│   └── ui.py         # shared workflow page template
└── workflows/
    └── <name>/
        ├── meta.yaml   # name, description, instructions (markdown), params, status
        └── pipeline.py # run(params: dict, progress) -> {"outputs": [...]}
                        # optional: get_inputs(params) -> {name: path}
```

## Workflow page layout (the same for every workflow)

Title + status badge → description → 📖 Instructions (markdown expander)
→ Parameters (compact row) → 📥 Inputs (existence + last-modified table,
auto-checked, "Check inputs" button) → ▶ Start with live log → Outputs
(download buttons) → Recent runs → 📜 Logs browser.

## Adding a new workflow

1. Create a `workflows/<name>/` directory.
2. Add `meta.yaml` (see existing examples) — until `status` is `ready`,
   the page shows only the description and instructions, no Start button.
3. Add `pipeline.py` with a `run(params, progress)` function — pure pandas
   logic, zero Streamlit code. Return `{"outputs": [output file paths]}`.
   Add `get_inputs(params)` so the Inputs table can check source files.
4. Set `status: ready`. That's it — UI, logs and history work automatically.

## Migration statuses

* `planned` — ⚪ not migrated yet (placeholder)
* `in_progress` — 🟡 migration in progress
* `ready` — 🟢 migrated, can be run

## Conventions

* All code, comments, docstrings, meta.yaml content and UI copy in English.
* The `period` parameter uses `type: period` — a dropdown whose default is
  derived from today minus 10 days (YYYYMM), with ±3 periods to choose from
  (`offset_days` / `span` in meta.yaml override the defaults).
