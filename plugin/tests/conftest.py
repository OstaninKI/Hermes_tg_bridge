"""Load the plugin exactly the way Hermes loads it.

Hermes imports a directory plugin as ``hermes_plugins.<slug>`` through
``importlib.util.spec_from_file_location`` with ``submodule_search_locations``
(hermes_cli/plugins.py:_load_directory_module). Reproducing that here means the
tests exercise the real import path — including whether ``__init__.py`` can reach
its sibling modules — instead of a plain ``sys.path`` import that would hide it.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes-telegram-user"
NAMESPACE = "hermes_plugins"
SLUG = PLUGIN_DIR.name.replace("-", "_")
MODULE_NAME = f"{NAMESPACE}.{SLUG}"


def load_plugin_package() -> types.ModuleType:
    """Import the plugin package the way the Hermes plugin manager does."""
    if NAMESPACE not in sys.modules:
        namespace = types.ModuleType(NAMESPACE)
        namespace.__path__ = []  # type: ignore[attr-defined]
        namespace.__package__ = NAMESPACE
        sys.modules[NAMESPACE] = namespace

    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]

    init_file = PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        init_file,
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = MODULE_NAME
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


load_plugin_package()

# Expose the adapter submodule under a short alias so tests can ``import adapter``.
sys.modules.setdefault("adapter", sys.modules[f"{MODULE_NAME}.adapter"])
