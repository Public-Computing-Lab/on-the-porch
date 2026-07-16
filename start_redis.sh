#!/usr/bin/env bash
# OBSOLETE: Redis is not supported by On the Porch.
#
# Shared cache, rate limits, semantic cache, and ingest job status use MySQL.
# Set CACHE_BACKEND=mysql (and optionally SEMANTIC_CACHE_BACKEND=mysql), then:
#
#     ./start_api.sh
#
set -euo pipefail
echo "ERROR: Redis is not supported." >&2
echo "Use CACHE_BACKEND=mysql and ./start_api.sh instead of start_redis.sh." >&2
exit 1
