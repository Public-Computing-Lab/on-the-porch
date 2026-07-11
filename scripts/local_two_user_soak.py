#!/usr/bin/env python3
"""Run a two-user, 10-minute local production-like soak test."""

from __future__ import annotations

import sys

from local_prod_sim import main


if __name__ == "__main__":
    raise SystemExit(main(["soak", *sys.argv[1:]]))
