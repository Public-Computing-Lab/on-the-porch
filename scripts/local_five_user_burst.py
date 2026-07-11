#!/usr/bin/env python3
"""Run a five-user, 3-minute local production-like burst test."""

from __future__ import annotations

import sys

from local_prod_sim import main


if __name__ == "__main__":
    raise SystemExit(main(["burst", *sys.argv[1:]]))
