# Architecture

TripPilot separates semantic decisions from deterministic execution. An LLM interprets the original request and chooses entity IDs; public tool facts and program logic produce a concrete itinerary. A separate evaluator measures constraint satisfaction after the Agent finishes.

## Initial planning

1. Preserve the original request alongside any translation. Extract supported requirements with exact evidence quotes; explicit room/bed counts can correct a contradictory prediction, with an audit record.
2. Query public attraction, restaurant, accommodation and transport catalogs. Bilingual names are linked through public record IDs. Candidate search is bounded.
3. Ask the model for an ID-only itinerary sketch. It does not author prices or invent entities.
4. Compile the sketch: copy facts, connect transport, schedule within opening/meal/return windows, calculate party-size costs and validate the plan.
5. Return a checked plan or feed compiler feedback into bounded sketch retries. If needed, use a ReAct fallback with the remaining logical-call and time budget.

The compiler requires an attraction and a meal per day as an explicit usability floor. This requirement is reported separately from the original benchmark score.

## Replanning and consent

A typed event represents earlier return, unavailable transport or a new budget. A proposed edit is located against an immutable seed snapshot and checked before an atomic version commit. Simple cases use public tool candidates; complex cases can invoke a model fallback.

One-day return can conflict with explicit lodging requirements. A version-bound clarification state records the original seed, question and choices. `cancel_overnight` waives lodging only after an explicit answer; `keep_original` cancels the revision. State checksum and seed-version checks prevent accidental stale-state reuse; these are consistency controls, not multi-tenant authentication.

## Tools and evaluation boundary

The model sees allowlisted read-only structured tools. It has no arbitrary code/file tool; query-loading and world-command endpoints are blocked. The runtime checker uses public rules and requirements predicted from user text. Hidden annotations are accessed only by offline scorers. The process shares the local filesystem, so this interface design must not be described as OS-enforced file isolation.

## Execution and accounting

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

`checked_ok` is a runtime result, not a hidden-constraint guarantee. Request errors, invalid input, worker failures, cancellation and hard timeouts are explicit outcomes.
