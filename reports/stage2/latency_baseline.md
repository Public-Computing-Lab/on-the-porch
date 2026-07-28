# Turn Observability Report

- Turns: **135**
- Success rate: **100.0%**
- Failure rate: **0.0%**
- Timeout rate: **0.0%**
- Latency: p50 **1776.6 ms**, p90 **2875.6 ms**, p95 **3434.7 ms**, p99 **4340.9 ms**

## By Execution Mode

| Mode | Turns | Success | p50 ms | p90 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| advisory | 1 | 100.0% | 39.1 | 39.1 | 39.1 | 39.1 |
| chitchat | 4 | 100.0% | 22.5 | 23.9 | 23.9 | 24.0 |
| history | 14 | 100.0% | 1263.4 | 1768.8 | 2188.2 | 2762.9 |
| rag | 63 | 100.0% | 1835.6 | 2609.6 | 2951.3 | 6501.8 |
| sql | 53 | 100.0% | 1998.6 | 3263.5 | 3592.2 | 4249.3 |

## Stage Durations

| Stage | Count | p50 ms | p90 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|
| answer_synthesis | 90 | 1313.4 | 1907.4 | 2099.6 | 3068.7 |
| answer_validation | 90 | 0.1 | 0.1 | 0.1 | 0.4 |
| authentication | 135 | 4.2 | 5.2 | 6.0 | 7.0 |
| bm25_retrieval | 209 | 0.1 | 0.2 | 0.3 | 0.3 |
| embedding | 88 | 22.9 | 34.2 | 35.3 | 1122.6 |
| follow_up_gate | 50 | 0.7 | 1.5 | 2.0 | 740.1 |
| history_answer | 14 | 1217.1 | 1722.5 | 2142.7 | 2719.0 |
| history_loading | 135 | 0.8 | 1.2 | 1.4 | 2.2 |
| interaction_logging | 135 | 7.2 | 9.8 | 10.6 | 11.4 |
| message_persistence | 270 | 2.7 | 9.6 | 10.3 | 12.0 |
| ownership_verification | 270 | 1.2 | 2.7 | 3.0 | 3.5 |
| plan_execution | 90 | 24.7 | 38.5 | 40.2 | 68.9 |
| planning | 109 | 0.1 | 0.4 | 427.8 | 1425.5 |
| rag_synthesis | 56 | 1361.0 | 1800.9 | 2040.5 | 2180.8 |
| routing | 109 | 0.2 | 827.7 | 933.6 | 1406.3 |
| sql_execution | 66 | 4.4 | 38.8 | 50.7 | 278.6 |
| sql_generation | 19 | 833.3 | 1027.9 | 1065.1 | 1202.1 |
| sql_repair | 13 | 730.3 | 1127.8 | 1240.7 | 1373.3 |
| sql_synthesis | 34 | 1114.3 | 2027.1 | 2625.3 | 3382.0 |
| table_selection | 19 | 511.4 | 573.2 | 670.0 | 1008.4 |
| thread_state_loading | 135 | 0.0 | 0.0 | 0.1 | 0.1 |
| thread_state_persistence | 135 | 1.9 | 3.4 | 3.8 | 4.9 |
| turn_execution | 135 | 1747.1 | 2831.7 | 3399.9 | 4301.3 |
| vector_retrieval | 209 | 0.5 | 0.9 | 1.1 | 1.2 |

Malformed input lines skipped: **0**
