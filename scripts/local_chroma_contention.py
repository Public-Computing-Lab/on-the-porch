#!/usr/bin/env python3
"""Run RAG reads during a local Chroma/Qdrant contention check."""

from __future__ import annotations

import sys

from local_prod_sim import main


if __name__ == "__main__":
    raise SystemExit(main(["chroma", *sys.argv[1:]]))
