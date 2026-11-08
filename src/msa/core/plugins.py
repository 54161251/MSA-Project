"""Discover and import every role's plugin module once."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

_loaded = False
loaded_plugins: list[str] = []


def load_plugins() -> list[str]:
    global _loaded
    if _loaded:
        return loaded_plugins
    _loaded = True
    app_dir = Path(__file__).resolve().parents[1]
    candidates = ["msa.safety_tools.plugin"]
    agents_dir = app_dir / "agents"
    if agents_dir.is_dir():
        for info in sorted(pkgutil.iter_modules([str(agents_dir)]), key=lambda i: i.name):
            if info.ispkg and (agents_dir / info.name / "plugin.py").exists():
                candidates.append(f"msa.agents.{info.name}.plugin")
    for mod in candidates:
        pkg_path = app_dir.parent / Path(*mod.split("."))
        if not pkg_path.with_suffix(".py").exists():
            continue
        importlib.import_module(mod)
        loaded_plugins.append(mod)
    return loaded_plugins
