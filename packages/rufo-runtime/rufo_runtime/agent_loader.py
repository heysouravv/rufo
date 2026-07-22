from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_agent(module_path: str | Path, entrypoint: str = "agent"):
    """Dynamically import a Python file and return its `entrypoint` attribute.

    The agent module is expected to expose a compiled LangGraph graph (or any
    object with a LangGraph-compatible `.invoke(input, config=...)` method)
    under the given attribute name.
    """
    module_path = Path(module_path).resolve()
    if not module_path.exists():
        raise FileNotFoundError(f"agent module not found: {module_path}")

    module_name = f"rufo_agent_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load agent module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, entrypoint):
        raise AttributeError(
            f"agent module {module_path} has no attribute '{entrypoint}'"
        )
    return getattr(module, entrypoint)
