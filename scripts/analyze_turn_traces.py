#!/usr/bin/env python3
"""Aggregate Stage 2 turn-observability JSONL without a live application."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ON_THE_PORCH = ROOT / "on_the_porch"
if str(ON_THE_PORCH) not in sys.path:
    sys.path.insert(0, str(ON_THE_PORCH))

from turn_observability_analysis import (  # noqa: E402
    aggregate_traces,
    load_traces,
    render_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_file", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    traces, input_stats = load_traces(args.trace_file)
    report = aggregate_traces(traces, input_stats=input_stats)
    markdown = render_markdown(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown, encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
