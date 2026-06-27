#!/usr/bin/env python3
"""
Deprecated scheduling wrapper — use cron_ingest.sh at the repo root instead.

Boston Open Data sync is the first step of ./cron_ingest.sh (crime, 311, shootings).
This script remains for older crontab entries and forwards to the full daily pipeline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    cron = repo_root / "cron_ingest.sh"
    if not cron.is_file():
        print(f"ERROR: {cron} not found", file=sys.stderr)
        sys.exit(1)
    print(f"Forwarding to {cron} (Boston sync is included)")
    result = subprocess.run(["/bin/bash", str(cron)], cwd=str(repo_root))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
