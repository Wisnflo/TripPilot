# Architecture

TripPilot separates semantic decisions from deterministic execution. An LLM interprets the original request and chooses entity IDs; public tool facts and program logic produce a concrete itinerary. A separate evaluator measures constraint satisfaction after the Agent finishes.

## Initial planning

1. Preserve the original request alongside any translation. Extract supported requirements with exact evidence quotes; explicit room/bed counts can correct a contradictory prediction, with an audit record.
2. Query public attraction, restaurant, accommodation and transport catalogs. Bilingual names are linked through public record IDs. Candidate search is bounded.
3. Ask the model to select catalog IDs for an itinerary sketch; the compiler copies prices and other attributes from tool records.
4. Compile the sketch: copy facts, connect transport, schedule within opening/meal/return windows, calculate party-size costs and validate the plan.
5. Return a checked plan or feed compiler feedback into bounded sketch retries. If needed, use a ReAct fallback with the remaining logical-call and time budget.

The compiler requires an attraction and a meal per day as an explicit usability floor. This requirement is reported separately from the original benchmark score.

## Replanning and consent

A typed event represents earlier return, unavailable transport or a new budget. A proposed edit is located against an immutable seed snapshot and checked before an atomic version commit. Simple cases use public tool candidates; complex cases can invoke a model fallback.

One-day return can conflict with explicit lodging requirements. A version-bound clarification state records the original seed, question and choices. `cancel_overnight` waives lodging only after an explicit answer; `keep_original` cancels the revision. State and seed-version checks detect stale revision state before resumption.

## Tools and evaluation boundary

The model sees allowlisted read-only structured tools. It has no arbitrary code/file tool; query-loading and world-command endpoints are blocked. The runtime checker uses public rules and requirements predicted from user text. Hidden annotations are accessed only by offline scorers. The parent passes only the public task, current seed and explicit event to a restricted worker. macOS uses a file access policy; Linux uses Landlock ABI ≥ 3. Workers can read the declared runtime/public tool paths and write only their task directory. Scoring annotations, offline reference plans and unrelated home files are outside the policy. The policy restricts file access while retaining network access to the model API.

## Execution and accounting

A task-local resource counter is shared by requirement extraction, planning, validation and fallback: up to 16 logical model calls, 500,000 returned tokens, 400 backend tool queries and 4 feedback opportunities. All public-packet preparation queries count. A conservative UTF-8 input preflight limits requested output against remaining tokens; missing provider usage cannot be counted exactly. Resource updates are journaled even if the process is later terminated.

Itinerary prices use decimal arithmetic with cent rounding. API usage estimates retain sub-cent precision before JSON serialization.

The parent supervises a child process group, terminates it on the task deadline and cleans it up on cancellation. The adapter clips request timeouts to the remaining budget and implements explicit retry/backoff. Every attempt emits start/success/error journal events without prompts or credentials.

Returned prompt/output/cache usage is aggregated without double charging cached input. Failed or killed requests may have unknown remote usage. List-price estimates are separate from an actual invoice; client-side termination cannot guarantee that the provider stops generation or billing.

## Output contract

| File | Contents |
| --- | --- |
| `job.json` | Requested operation and execution settings |
| `record.json` | Terminal result, process supervision, ledger and validation scope |
| `plan.json` | Plan produced by a successful operation, or explicitly preserved original |
| `task.json` | Public task used for the operation |
| `state.json` | Clarification/revision state when available |
| `requests.jsonl` | Per-attempt timing, status and returned usage |
| `resources.jsonl` | Durable model/token/tool/feedback counters |
| `access-policy.json` | Declared worker file access |

`checked_ok` is a runtime result, not a hidden-constraint guarantee. Request errors, invalid input, worker failures, cancellation and hard timeouts are explicit outcomes.
