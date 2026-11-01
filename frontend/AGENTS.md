# Client contribution guide

Read the repository guide and `frontend/README.md`. Keep UI source and its bundled assets together: use `npm ci`, `npm run test:ui`, and `npm run build:app`. The Python demo serves the bundled client; its same-origin session and host/origin checks must remain intact.

Preserve itinerary creation, revision, explicit conflict answers, cancellation, evidence disclosures and actual-record replay. Keep illustrative screen data visibly separate from real results. Do not fabricate successful API responses, verified preferences, travel facts, quotes or bookings.

API credentials stay in the Python parent environment. Do not embed configuration, personal paths or keys in client code. Retain dependency licenses and generated-asset provenance.

Do not modify source, configuration or bundles during a running model evaluation. Preserve prior evidence. After behavior changes, run the relevant Python checks and client state regressions; check desktop and narrow-screen interactions in the in-app browser. Record concrete changed files, commands, outcomes, active jobs, limitations and next steps when handing off.
