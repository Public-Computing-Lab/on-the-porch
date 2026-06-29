"""
Shim: re-export root config for scripts that `import config` from this directory.

Canonical module: ``config`` at the project root.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "config.py"
_spec = importlib.util.spec_from_file_location("_otp_root_config", _CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load root config from {_CONFIG_PATH}")

_root = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root)

for _name, _value in _root.__dict__.items():
    if _name.startswith("_"):
        continue
    globals()[_name] = _value

Config = _root.Config
