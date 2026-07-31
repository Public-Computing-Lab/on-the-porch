# Retry Policy

`on_the_porch/retry_policy.py` is the single transport retry policy for Stage 4. It permits
one initial attempt and at most one retry. Existing fallback-model selection consumes the
second attempt; nested provider layers may not restart another retry series.

Retryable categories are provider 429, provider 5xx, connection reset, transient network
failure, and a short provider timeout when sufficient working budget remains. Authentication,
ownership, conflict, invalid input, permanent 4xx, SQL-safety rejection, parser failure,
capacity rejection, and deadline exhaustion are never retried.

The policy checks the authoritative turn budget before the first attempt, before backoff,
and before a retry. Backoff and optional jitter are capped so the retry's minimum execution
window still fits without consuming the finalization reserve. Retry decisions record only
normalized categories, attempt number, and bounded backoff; raw prompts, SQL, responses,
and provider errors are excluded.

Dynamic-SQL semantic correction is not a transport retry. It remains limited to one repair,
must fit the same budget, and must pass the existing AST safety validator before execution.
