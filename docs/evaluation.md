# Evaluation and reproducibility

## Scope and task selection

The primary study uses 200 independent ChinaTravel parent tasks: 70 easy, 60 medium and 70 human. Task selection excludes 255 previously exposed project parents. Selection seed 2026103107 fixes the task order, 24 component parents, 12 repeat parents and 12 second-model parents before the corresponding outcomes. Repeated runs and overlapping subsets do not create additional independent parents.

Initial planning uses the original schema, commonsense and hard-constraint evaluators. A separately reported usability floor requires an attraction and a meal on each day. Runtime `checked_ok` verifies public rules and predicted requirements; it does not guarantee complete benchmark success.

The primary provider alias is `qwen3.8-reasoner`; the second is `glm-5.3-flash`. These are service identifiers. The provider does not expose fixed model weights or a reproducible sampling configuration. Published tasks may also have appeared in model pretraining.

## Methods and shared limits

| Identifier | Method |
| --- | --- |
| `b0` | Single model response producing a complete plan |
| `b1` | ReAct with public tools |
| `b2` | ReAct with public tools and runtime-check feedback |
| `structured` | Requirement extraction, ID-only sketch selection, factual compilation and bounded feedback/fallback |
| `rules` | Deterministic bounded repair without model calls |
| `patch` | Deterministic verified repair first, then ReAct fallback with the remaining task budget |

All initial methods receive the same deterministic public fact packet and factual completion of missing local routes. Preparation, validation and fallback consume the task's resource limits. Equal available information and limits do not imply identical query strategies.

| Resource | Initial planning | Conditional repair |
| --- | ---: | ---: |
| Logical model calls | 16 | 16 |
| Returned tokens | 500,000 | 500,000 |
| Backend queries | 400 | 400 |
| Feedback opportunities | 4 | 4 |
| Maximum output tokens per request | 10,000 | 10,000 |
| Task wall-clock deadline | 360 s | 240 s |
| Request timeout, clipped to remaining time | 180 s | 180 s |
| Maximum attempts per request | 2 | 2 |
| Concurrent tasks | 4 | 4 |

Input preflight uses a conservative estimate. Every returned usage record is retained; failed or terminated requests may incur unobserved remote usage. Request retries honor provider reset times within the existing task deadline. Authentication failures, unrecovered rate limits and consecutive zero-usage failures stop scheduling.

The runtime worker receives only the public task, seed plan and allowlisted event fields. Offline scoring annotations and constructive reference plans are outside its file-access policy. macOS file restrictions and Linux Landlock enforce this separation; the latter requires ABI ≥ 3.

## Initial planning: 200 paired parents

| Method | Complete success | Success plus usability | Returned tokens | Summed task seconds | Median seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Single response | 6/200 | 5/200 | 4,055,288 | 22,882.83 | 105.27 |
| ReAct | 29/200 | 23/200 | 26,607,265 | 59,664.28 | 358.61 |
| ReAct + checker | 55/200 | 50/200 | 29,785,729 | 64,248.86 | 360.02 |
| TripPilot | 142/200 | 142/200 | 7,516,798 | 37,666.30 | 135.84 |

TripPilot succeeds on 71.0%, compared with 27.5% for ReAct + checker: a paired difference of 43.5 percentage points. The parent-paired bootstrap 95% interval is 35.5–51.0 percentage points. Exclusive successes are 92 versus 5; exact two-sided McNemar p=8.59077e-22.

Returned tokens decrease by 74.8% and summed task time by 41.4%; parent-paired 95% intervals are 70.8%–78.3% and 36.8%–45.9%. Across the 50 parents where both methods succeed, tokens are 1,255,141 versus 8,107,074 and time is 6,017.78 versus 12,565.44 seconds.

TripPilot makes more backend queries: 53,884 versus 44,686. Hard timeouts are 45 versus 130, and possible unobserved billing affects 46 versus 135 tasks. The comparison measures complete systems within a common 360-second budget; it does not establish unlimited-time reasoning superiority or a complete billing reduction.

| Difficulty | TripPilot | ReAct + checker |
| --- | ---: | ---: |
| easy | 49/70 | 21/70 |
| medium | 50/60 | 22/60 |
| human | 43/70 | 12/70 |

## Conditional repair: 100 feasible events

An offline constructor verifies at least one complete feasible reference plan before running repair methods. It uses public tools and a separate search, without calling the compared repair proposer. Targets are 34 earlier-return, 33 unavailable-transport and 33 budget-reduction events; the completed cohort spans 56 parents with no shortfall. A separate review revalidated all 100 references.

Parent ordering, difficulty quotas and seed priority (`b2`, `b1`, `b0`, `structured`) are fixed before repair outcomes. Each method starts with exactly the same seed and event. Reference plans remain offline. Known feasibility describes this constructed cohort and does not prove global search completeness.

| Method | Complete success | Success plus usability | Returned tokens | Summed seconds | Median seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Deterministic repair | 66/100 | 66/100 | 0 | 56.85 | 0.62 |
| ReAct + checker | 88/100 | 86/100 | 7,136,238 | 10,013.09 | 76.39 |
| TripPilot repair | 85/100 | 84/100 | 4,055,073 | 5,486.67 | 0.67 |

TripPilot's repair success is 3 percentage points lower. The parent-cluster bootstrap 95% interval is −9.09 to +3.30 percentage points; this does not establish greater success or quality equivalence. Returned tokens decrease by 43.2% and summed time by 45.2%, with intervals 28.8%–56.0% and 34.9%–55.3%. Resampling uses 56 parent clusters, not 100 independent event pairs; an event-level McNemar test is not applied.

| Event | TripPilot repair | ReAct + checker |
| --- | ---: | ---: |
| Earlier return | 34/34 | 33/34 |
| Unavailable transport | 32/33 | 33/33 |
| Budget reduction | 19/33 | 22/33 |

The 80 jointly successful events provide a resource comparison on the same outcomes:

| Metric | TripPilot repair | ReAct + checker |
| --- | ---: | ---: |
| Returned tokens | 1,444,103 | 4,599,547 |
| Summed seconds | 1,871.63 | 6,308.89 |
| Removed/modified plus added/modified activities | 501 | 882 |
| Unaffected activities retained, same denominator | 329/334 | 203/334 |

Full activity content and day are matched as multisets, so deletion does not shift subsequent matches. Entity metrics measure public name/transport-ID membership, not complete factual correctness. Both require submission denominators; statistics over different sets of returned plans should not be treated as matched comparisons.

Sixty-six successful repairs use zero model requests. Thirty-three events have journaled model attempts; the final result metadata explicitly marks only 21 fallbacks because 12 forced terminations lose their return metadata. The metadata count is a lower bound. These deterministic fast-path gains should not be attributed to an LLM selector.

One-day return can conflict with requested lodging. A simulated explicit answer is supplied only after the same clarification step. Offline adaptations rewrite only the event-covered day count; pure lodging conditions are waived only with recorded consent. Mixed conditions remain. This is conditional repair from valid plans, not end-to-end travel-planning success or official dynamic benchmark gold.

## Component controls, repeats and second model

| Setting, same 24 parents | Complete success | Returned tokens | Summed seconds | Hard timeouts |
| --- | ---: | ---: | ---: | ---: |
| Structured sketch + compiler + feedback | 18/24 | 957,121 | 3,880.99 | 4 |
| Structured sketch + compiler, no feedback | 15/24 | 462,966 | 2,515.27 | 0 |
| Full-plan JSON + checker feedback | 1/24 | 1,937,785 | 8,616.29 | 23 |

The structured setting exceeds no-feedback by 12.5 percentage points, with a parent-paired 95% interval of −8.33 to +33.33 points; exclusive successes are 5 versus 2, exact McNemar p=0.453125. Feedback uses more returned tokens and time on this subset. The sample does not establish a reliable feedback benefit.

Against full-plan JSON, the difference is 70.83 points (95% interval 54.17–87.50), exclusive successes 17 versus 0, exact p=1.52588e-5. Twenty-three full-plan tasks reach the common deadline. This is a comparison of output strategies within the declared time/output budgets, not a universal proof that compilation outperforms direct generation. Intervals are exploratory, without a multiple-comparison adjustment.

The original component control records are retained for diagnosis and excluded from attribution: feedback state could be overwritten and the full-plan retry contained a conflicting output instruction. Corrected controls rerun all three settings on the same 24 predetermined parents with identical limits. Their results are exploratory and do not add independent test parents.

| Fixed 12 parents | Single response | ReAct | ReAct + checker | TripPilot |
| --- | ---: | ---: | ---: | ---: |
| Initial run subset | 1/12 | 2/12 | 3/12 | 8/12 |
| Additional run 1 | 0/12 | 0/12 | 4/12 | 8/12 |
| Additional run 2 | 0/12 | 1/12 | 6/12 | 9/12 |

| Second service, fixed 12 parents | Complete success | Returned tokens | Summed seconds | Hard timeouts |
| --- | ---: | ---: | ---: | ---: |
| TripPilot | 4/12 | 97,654 | 3,714.33 | 8 |
| ReAct + checker | 0/12 | 0 returned | 4,320.34 | 12 |

The second service is `glm-5.3-flash`. All 24 executions are retained, including an initial 13 executions before a protective stop and the 11 previously unexecuted tasks completed after one same-protocol continuation. Exclusive successes are 4 versus 0 (exact two-sided p=0.125). Twenty tasks hit the deadline; possible unobserved billing affects 9 TripPilot and 12 comparison tasks. Zero returned tokens do not mean zero provider work or cost. This small, timeout-dominated result does not establish robust cross-model generalization.

## Failure review and accounting

Nine returned TripPilot initial plans pass public checks but fail complete hard constraints. Offline inspection identifies specific cuisine/hotel categories, a missing conjunctive attraction type, an implicit local-cuisine preference, an unsupported public-transport preference and broad West Lake name matching. They remain failures; checking predicted constraints does not verify that extraction captured the entire request.

Dynamic TripPilot failures include 12 hard timeouts, two public-check successes failing complete constraints and one worker error; ReAct + checker has nine hard timeouts and three complete-constraint failures. The worker error and a matching deterministic-repair error come from a non-finite public fare. A later input-boundary correction is regression-tested; it does not replace those measured failures.

Known-usage list-price estimates are 5.739723 versus 11.629979 CNY for initial planning and 1.516295 versus 2.876372 CNY for repair. Dynamic possible unobserved billing affects 12 versus 13 tasks. Rates have an observed date and are not an invoice; killed or failed requests may still be charged remotely. Missing billing is never asserted to be zero.

## Evidence and implementation provenance

Each study retains its execution protocol, per-task scores, records, request attempts and durable resource counters. Initial planning and repeated runs describe implementation `0e87c1a`; repair and the second model describe `99fbc71`, which changes request-limit recovery. The corrected component study describes implementation `6b53eea`. Later source corrections do not retroactively change measured results.

The earlier service-interrupted repair execution and invalid-control records remain separate. No completed failure was selectively retried or replaced in the current comparison. Local paths, credentials and private endpoint identifiers are removed from published copies; diagnostic non-finite numbers are encoded as strings for strict JSON. Original local evidence is retained. The four earlier published study directories remain byte-for-byte unchanged, including 30 legacy JSON files with non-finite diagnostic values accepted by Python’s reader. Strict JSON normalization applies to the newly exported studies; no legacy score or output is rewritten.

- [Machine-readable results](../evaluation/results.json)
- [Per-study evidence](../evaluation/runs/)
- [Feasible repair cohort](../evaluation/cohorts/repair-100.json)
- [Offline reference plans](../evaluation/references/repair-100/)
- [Published content inventory](../evaluation/checksums.json)

## Reproduce

Offline checks do not call a model:

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -q
uv run --locked python scripts/check_isolation.py
uv run --locked python scripts/verify_release.py
```

For a new complete evaluation, configure an available compatible API and choose a fresh identifier:

```sh
uv run --locked python scripts/run_completion.py --prefix reproducible-study
```

The complete workload contains 800 initial executions, 72 component executions, 96 repeat executions, 300 constructive repair executions and 24 second-model executions. At four workers, the sum of task deadlines gives a 29.8-hour scheduling ceiling before preparation/scoring overhead, not a predicted runtime. Service delays and stops can extend elapsed completion time.

Inspect state and logs before using `--resume`. Completed outcomes are skipped, including failures. An incomplete attempt with a journal is retained as interrupted instead of purchasing another attempt. Source, tool data or configuration changes require a new identifier. A run lock prevents concurrent parents from writing the same study.

The bundled ChinaTravel environment covers ten example cities and does not provide real-time booking. Candidate search is bounded; failure is not proof of infeasibility. No NeSy/SOTA comparison is claimed. ChinaTravel supplies public tools, data and the original evaluators; TripPilot supplies its planning, compilation, repair, consent and supervised execution design. See [third-party notices](../THIRD_PARTY_NOTICES.md).
