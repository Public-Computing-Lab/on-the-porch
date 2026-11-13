"""Gunicorn entrypoint for DreamHost / legacy `-c ./gunicorn.conf.py` startups.

Delegates to api/gunicorn.conf.py so GUNICORN_* values in .env apply everywhere.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_API_CONF = Path(__file__).resolve().parent / "api" / "gunicorn.conf.py"
_spec = importlib.util.spec_from_file_location("gunicorn_api_conf", _API_CONF)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load gunicorn config from {_API_CONF}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _name, _value in vars(_mod).items():
    if _name.startswith("_"):
        continue
    globals()[_name] = _value
