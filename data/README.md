# Frozen benchmark data

ChinaTravel queries and sandbox tables are licensed under CC BY 4.0. See [attribution and versions](../THIRD_PARTY_NOTICES.md).

| Directory | Purpose |
| --- | --- |
| `agent_visible/` | Allowlisted public task fields passed to the Agent |
| `scoring/` | Hidden benchmark constraints, read only by offline scoring scripts |
| `raw_queries/` | Original public benchmark query CSVs, including annotations |
| `manifests/` | Input provenance, counts and original CSV checksums |

The Agent has no arbitrary file-reading tool; its tool allowlist blocks query/world-command endpoints. Scoring files must never be injected into prompts, tool results or runtime requirement checks. This is an interface boundary, not an operating-system security sandbox.

The bilingual tool tables are included under `upstream/ChinaTravel/chinatravel/environment/database{,_en}/`. Verify data and code using `uv run --locked python scripts/verify_release.py`.
