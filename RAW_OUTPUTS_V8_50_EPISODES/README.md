# Kuku Promo Lab — Full 50-Episode Raw Outputs (Problem 1)

This folder contains the unedited outputs from running Kuku Promo Lab V8 (`kuku promos`) across all 50 episodes of the assignment screenplay pack.

- `promos.csv` / `promos.json`: All **52 ranked promos** across the 50 episodes (15 Tier-1 `post_first`, 21 `good`, 16 `weak`), with episode number, exact start/end timestamps for every segment, duration, opening hook (0–3 s) + verbatim quote, unresolved cliffhanger + verbatim quote, one-line `why_it_works`, `why_ranked`, cold-viewer scores (`hook`, `cliffhanger`, `standalone`, `tap_to_watch`), and quality flags.
- `report.html`: Self-contained interactive HTML viewer for browsing all 52 ranked cuts, full clip transcripts, alternates, rejected cuts, and blind baseline comparisons.
- `alternates.json`: 5 valid cuts kept as alternates (>50% overlap with a higher-ranked cut or >2/episode cap).
- `rejected.json`: 8 cuts rejected by deterministic rule checks even after 1 structured repair pass.
- `baselines.json`: 100 naive baseline cuts (50 episode-ending 40 s cuts + 50 random 40 s cuts) evaluated blind alongside the tool's candidates.
- `quality.json`: Aggregate evaluation metrics (blind cold-viewer means, 31–5 head-to-head win record over episode endings, Spearman correlation `r = 0.293` between proposer self-score and blind cold critic).
- `run_manifest.json`: Full provenance (pack SHA-256, model configuration, token usage, prompt digest).
- `model_calls/`: Raw JSON request/response envelopes for the proposer, repair, and blind critic calls.
- `historical_v1_full_pack/` & `historical_v3_gate3/`: Preserved earlier local `Qwen3-8B`/`4B` runs.
