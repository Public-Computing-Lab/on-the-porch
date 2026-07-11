#!/usr/bin/env python3
"""Run local production-like smoke checks against a running API."""

from __future__ import annotations

import sys

from local_prod_sim import main


if __name__ == "__main__":
    raise SystemExit(main(["smoke", *sys.argv[1:]]))
