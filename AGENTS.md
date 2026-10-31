# Contributor and execution-agent guide

Read README.md, docs/architecture.md and docs/evaluation.md before changing behavior. Use the project-specific uv environment: `uv sync --locked`, then `uv run --locked python ...`. Do not install into system Python or a neighboring environment.

- Keep the planner, tool boundary, execution supervision and offline evaluator separate. Runtime code must not read scoring annotations or import offline judges.
- Preserve original-language user requests. Tool facts and bilingual links must come from public records.
- Require explicit consent for incompatible lodging changes. Validate the clarification state and original plan version before resuming.
- Model extraction and fallback consume the shared task budget. Record failed attempts and unknown billing; do not silently reset budgets.
- Treat published evaluation tasks as exposed. Method changes require newly selected and preregistered development/test splits; do not tune against the final labels and reuse the published score as independent evidence.
- Do not overwrite evaluation/runs or edit published scores to fit expectations. New runs need new identifiers; retain all failures and timeouts.
- Run `uv run --locked python -m unittest discover -s tests -q`, `scripts/check_isolation.py` and `scripts/verify_release.py` for relevant changes. The first two are offline; model evaluation is a separate explicit operation.
- Never commit .env, API keys, account details, virtual environments or local machine paths.

When handing work to another contributor or agent, record the objective, changed files, exact verification commands and outcomes, run IDs, active jobs, known limitations and the next concrete step. Keep private operational notes outside the public repository. Do not claim tests, benchmarks or user consent that did not occur.
