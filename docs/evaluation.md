# Evaluation and reproducibility

## Methods and protocol

`structured` is TripPilot's initial requirement/sketch/compiler planner; `patch` is the transactional incremental repair method. `b2` is a ReAct agent with a runtime checker and revision feedback. Both see the same public request, tool interfaces and, for dynamic tasks, seed plan and event.

The preregistered configuration contains 6 development and 24 test parents, selected before implementing the evaluated method and excluding earlier project runs. The test split has 8 easy, 8 medium and 8 human tasks. The model is the provider alias `qwen3.8-reasoner`; the provider does not expose a weight version or fixed sampling configuration.

| Setting | Initial planning | Dynamic repair |
| --- | ---: | ---: |
| Logical model calls | 16 | 16 |
| Task wall-clock deadline | 360 s | 240 s |
| Request timeout | 90 s | 90 s |
| Maximum attempts/request | 2 | 2 |
| Concurrent jobs | 4 | 4 |

Extraction and fallback share the logical-call budget. Programmatic tool work is included in wall-clock time; there is no equal tool-call-count cap, and query scheduling differs by method. Comparisons measure the complete control design, not isolated LLM reasoning quality.

## Initial planning

| Metric | TripPilot | ReAct + checker |
| --- | ---: | ---: |
| Original benchmark all-pass | 17/24 (70.8%) | 9/24 (37.5%) |
| All-pass plus usability floor | 17/24 | 3/24 |
| Returned tokens | 561,673 | 2,511,093 |
| Summed task wall time | 4,088.18 s | 7,130.68 s |
| Median task wall time | 134.96 s | 303.67 s |
| Known-usage list-price estimate, CNY | 0.4393 | 1.0216 |
| Tasks with possible unobserved billing | 10 | 18 |

All-pass requires the original schema, commonsense and hard checks plus a completed submission. The additional usability floor requires at least one attraction and one meal each day, applied equally to both methods; it is not an official benchmark metric.

| Difficulty | TripPilot | ReAct + checker |
| --- | ---: | ---: |
| easy | 6/8 | 4/8 |
| medium | 7/8 | 2/8 |
| human | 4/8 | 3/8 |

| Runtime terminal state | TripPilot | ReAct + checker |
| --- | ---: | ---: |
| checked_ok | 17 | 13 |
| checked_with_violations | 0 | 1 |
| hard_timeout | 4 | 6 |
| request_error | 3 | 4 |

Runtime `checked_ok` does not guarantee original hidden-constraint all-pass. Errors and timeouts remain in the denominator and are not all attributable to planning ability.

Wilson 95% intervals are 50.8%–85.1% vs 21.2%–57.3%. Paired exclusive successes are 11 vs 3; exact two-sided McNemar p=0.0574. Parent-paired bootstrap intervals for token and summed-time reductions are 63.3%–86.2% and 25.9%–56.3%, respectively (3,000 resamples, seed 20261031). These results do not establish general superiority.

## Repeated runs

Two parents per difficulty were selected before observing results, with two additional runs. Repeated attempts do not add independent parents.

| Run | TripPilot | ReAct + checker |
| --- | ---: | ---: |
| Selected parents, initial run | 6/6 | 1/6 |
| Additional run 1 | 6/6 | 3/6 |
| Additional run 2 | 6/6 | 2/6 |

## Conditional dynamic repair

Parents are selected by a fixed UID hash order from successful structured plans with multiple days and intercity transport, up to 4 per difficulty. Actual selection is 4 easy, 4 medium, 3 human parents. Each produces all three event types; candidates are not filtered by revised-plan outcomes or proven feasibility. The budget event uses floor(95% of seed spend).

| Event | TripPilot | ReAct + checker |
| --- | ---: | ---: |
| Earlier return | 10/11 | 8/11 |
| Unavailable transport | 11/11 | 10/11 |
| Budget reduction | 6/11 | 4/11 |
| Total | 27/33 | 22/33 |

Returned tokens are 951,153 vs 1,555,663 (38.9% fewer); summed repair time is 2,593.23 vs 4,285.81 s (39.5% less). Parent-cluster bootstrap intervals for reductions are 14.8%–56.9% and 26.0%–49.6%. Runtime states are 28 checked_ok/5 hard_timeout for TripPilot and 23 checked_ok/2 checked_with_violations/8 hard_timeout for the baseline.

This is a **conditional repair** comparison, not end-to-end success. The initial 17/24 success and the one-parent selection shortfall remain visible.

Dynamic scoring is derived, not official gold. Only explicit event-covered day-count equalities are rewritten. One-day return with explicit lodging requirements triggers clarification; the benchmark simulates an explicit user cancellation only after a question. Pure lodging clauses are waived only if the actual result records the accepted choice, question and explicit waiver. Mixed lodging/budget clauses are retained. Preliminary and stricter-review scores are both published; the stricter score remains 27/33 vs 22/33 without rerunning models.

## Evidence and integrity

- [Machine-readable metrics](../evaluation/results.json)
- [Original per-run protocols, scores, records and request journals](../evaluation/runs/)
- [Dynamic selection recipe](../evaluation/dynamic-cohort.json)
- [Verification summary](../evaluation/verification.json)
- [Published file checksums](../evaluation/checksums.json)

Four formal stages have identical recorded source hashes. Core `src/` files are byte-identical to the evaluated snapshot. Publication adds documentation/packaging, renames test files and omits private operational material. Original protocol hashes describe the evaluation-time source snapshot; they are not checksums for the public repository tree. Local absolute paths in published JSON have been replaced by `{PROJECT_ROOT}`; published checksums cover these sanitized bytes. Original local records were retained unchanged.

The runtime rechecks task/seed fingerprints, not every sandbox table per task. Source and frozen data in this repository can be checked offline; neither API outputs nor future provider weights are guaranteed to replay bit-for-bit. Published tasks may have appeared in model pretraining.

## Reproduce

Offline integrity and interface checks:

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -q
uv run --locked python scripts/check_isolation.py
uv run --locked python scripts/verify_release.py
```

To repeat the complete model evaluation with the tested model alias, configure `.env`, then choose a fresh prefix:

```sh
uv run --locked python scripts/run_acceptance.py --prefix reproduction-001
```

This calls the configured API and incurs usage. It runs 24 paired initial tasks, two paired repeats on 6 fixed parents, and a newly derived conditional dynamic cohort. Each method has an atomic record; failures stay in the denominator. A new prefix prevents existing evidence from being overwritten.

For a different model, run individual stages using `run_suite.py --help` and an explicit `--model`; keep the task manifest, budgets and offline scoring the same, and report it as a separate comparison. `run_acceptance.py` uses the evaluated default alias.

If interrupted, inspect the stage state/logs under `experiments/v3/acceptance-<prefix>/`. Reissue the incomplete stage with the same source, manifest, settings and run ID; completed method records are skipped. If the code/config changed, use a new run ID. Run `score_suite.py --run-id <id>` only after the stage completes. The orchestrator refuses an existing acceptance directory rather than restarting a completed or partial pipeline silently.

## Scope and attribution

The benchmark is small, uses one provider alias and frozen data, and has no NeSy/SOTA comparison. Candidate search is bounded and does not prove global optimality/infeasibility. Tool access is allowlisted; the process is not an OS security sandbox. List prices are current USTC platform rates for observed usage, not an invoice or actual cash payment. Failed/pending requests may still incur unknown remote charges.

ChinaTravel supplies the environment, public tables and base evaluators. TripPilot supplies the structured planning/compiler control, transactional repair/clarification, shared execution budgets and evidence handling. See [third-party notices](../THIRD_PARTY_NOTICES.md).
