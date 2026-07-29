"""
Workflow registry.

Each workflow is a subdirectory of `workflows/` containing:
  * meta.yaml    — name, description, instructions (markdown), parameter definitions
  * pipeline.py  — function run(params: dict, progress: Callable[[str], None]) -> dict

Adding a new workflow = a new directory with these two files. Nothing else.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


@dataclass
class Workflow:
    key: str                      # directory name, e.g. "create_us_data"
    name: str                     # display name
    description: str = ""
    instructions: str = ""        # markdown shown on the workflow page
    owner: str = ""
    category: str = "Other"
    status: str = "planned"       # planned | in_progress | ready
    params: list[dict] = field(default_factory=list)
    path: Path = None

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def load_pipeline(self):
        """Import the workflow's pipeline.py and return the module."""
        pipeline_path = self.path / "pipeline.py"
        module_name = f"workflows.{self.key}.pipeline"
        # workflow dir goes on sys.path so local imports (e.g. config) resolve
        if str(self.path) not in sys.path:
            sys.path.insert(0, str(self.path))
        spec = importlib.util.spec_from_file_location(module_name, pipeline_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


def discover() -> list[Workflow]:
    """Scan the workflows/ directory and return workflows that have meta.yaml."""
    result = []
    for d in sorted(WORKFLOWS_DIR.iterdir()):
        meta_file = d / "meta.yaml"
        if not d.is_dir() or not meta_file.exists():
            continue
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
        result.append(Workflow(
            key=d.name,
            name=meta.get("name", d.name),
            description=meta.get("description", ""),
            instructions=meta.get("instructions", ""),
            owner=meta.get("owner", ""),
            category=meta.get("category", "Other"),
            status=meta.get("status", "planned"),
            params=meta.get("params", []),
            path=d,
        ))
    return result
