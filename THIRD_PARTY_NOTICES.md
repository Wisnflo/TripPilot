# Third-party notices

## ChinaTravel environment and evaluators

- Source: [LAMDA-NeSy/ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel)
- Pinned upstream commit: `f445e0011f42594fcc29b8c752ece06ded9a8218`
- Code license: MIT; the original copyright and license are retained in `upstream/ChinaTravel/LICENSE`.
- The vendored upstream implementation is retained for reproducibility. TripPilot wraps the read-only adapter, normalizes public responses and plans, and adds its own planner, compiler, repair logic, execution supervision and offline scoring orchestration. ChinaTravel supplies the environment, benchmark data and original evaluators.

## Query and sandbox data

- Official queries: [ChinaTravel dataset](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel)
- Official sandbox: [ChinaTravel-Sandbox](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox), release `2026.08.2`.
- Source mirror: [Cbphcr/ChinaTravel](https://modelscope.cn/datasets/Cbphcr/ChinaTravel), snapshot `04e51969aa0d63cbe8b956e2f2cba0a9d9109102`; [sandbox mirror](https://modelscope.cn/datasets/Cbphcr/ChinaTravel-Sandbox).
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
- Attribution: LAMDA-NeSy / ChinaTravel contributors. The data is redistributed as a reproducible research environment, with no endorsement by the original authors.
- Changes: original query CSVs are retained; TripPilot derives allowlisted public JSON inputs and separate offline annotation files. Sandbox records retain the source fields; bilingual names are linked through public IDs in TripPilot's adapter.

Dataset provenance appears in `data/manifests/`; the complete published file inventory appears in `evaluation/checksums.json`. Dataset licenses apply independently of the MIT license for TripPilot code. Python dependencies retain their respective licenses.

## Web interface

React and Phosphor Icons use MIT licenses; Noto Sans SC uses the SIL Open Font License. The progress indicator uses React Circular Progressbar under MIT. Retained notices are in [frontend/licenses](frontend/licenses/). The decorative West Lake panorama was generated for TripPilot; it is not a live destination photograph or a source of travel facts.
