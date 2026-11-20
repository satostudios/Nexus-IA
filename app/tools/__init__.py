"""NEXUS tools. Importing this package registers every tool in ``REGISTRY``.

To add a tool module, create it next to these and add it to ``_MODULES``.
"""

from __future__ import annotations

import importlib

from app.tools.registry import REGISTRY, Safety, ToolRegistry

_MODULES = (
    "app.tools.applications",
    "app.tools.web",
    "app.tools.filesystem",
    "app.tools.system",
    "app.tools.screenshots",
    "app.tools.input_control",
    "app.tools.memory_tools",
    "app.tools.creation",
)


def load_all() -> ToolRegistry:
    for name in _MODULES:
        importlib.import_module(name)
    return REGISTRY


__all__ = ["REGISTRY", "Safety", "ToolRegistry", "load_all"]
