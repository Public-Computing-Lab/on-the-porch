#!/usr/bin/env python3
"""Verify authoritative freeze hashes against current source files.

Recipe documentation lives in on_the_porch/authoritative_runtime_freeze.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "on_the_porch"), str(ROOT)]

from authoritative_runtime_freeze import (  # noqa: E402
    DEFAULT_FREEZE_PATH,
    compute_freeze_hashes,
    verify_authoritative_freeze,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--freeze",
        type=Path,
        default=DEFAULT_FREEZE_PATH,
        help="Path to the committed authoritative freeze JSON",
    )
    parser.add_argument(
        "--print-computed",
        action="store_true",
        help="Print the computed hash bundle as JSON",
    )
    args = parser.parse_args(argv)
    ok, report = verify_authoritative_freeze(args.freeze, root=ROOT)
    if args.print_computed:
        print(json.dumps(compute_freeze_hashes(root=ROOT), indent=2, sort_keys=True))
    if not ok:
        print("AUTHORITATIVE FREEZE MISMATCH", file=sys.stderr)
        print(json.dumps(report["mismatches"], indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print("authoritative runtime freeze hashes verified")
    print(f"freeze={report['freeze_path']}")
    print(f"prompt={report['computed']['planner_prompt_version']}")
    print(f"contract={report['computed']['planner_contract_hash'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
