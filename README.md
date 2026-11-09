# Kuku Promo Lab — Script-to-Promo Engine

**Extracts standalone 20–60 second cliffhanger promos from timestamped screenplays (`.zip` or `.txt`), enforces speech-complete boundaries in code, and ranks every cut via a blind cold-viewer critic.**

Built for the **Kuku AI Generalist Assignment — Problem 1: Find the Promos (Ads)**.

---

## Live Submission Links & Deliverables

| Deliverable | Link |
| :--- | :--- |
| **Live Interactive Prototype (`Play Cut` Monitor + `.ZIP` Runner)** | **[https://sanjayj23.github.io/kuku-promo-lab/](https://sanjayj23.github.io/kuku-promo-lab/)** |
| **Two-Page Solution Document (PDF)** | **[`FINAL_SOLUTION.pdf`](FINAL_SOLUTION.pdf)** ([Direct Pages Link](https://sanjayj23.github.io/kuku-promo-lab/FINAL_SOLUTION.pdf)) |
| **Unedited Raw Outputs (50-Episode Pack)** | **[`RAW_OUTPUTS_V8_50_EPISODES/`](RAW_OUTPUTS_V8_50_EPISODES/)** ([`RAW_OUTPUTS_V8_50_EPISODES.zip`](RAW_OUTPUTS_V8_50_EPISODES.zip)) |
| **Full HTML Evaluation Report** | **[https://sanjayj23.github.io/kuku-promo-lab/v8-50ep-report.html](https://sanjayj23.github.io/kuku-promo-lab/v8-50ep-report.html)** |
| **Complete Runnable Source Bundle** | **[`PUBLIC_SOURCE.zip`](PUBLIC_SOURCE.zip)** |
| **Static Prototype Bundle** | **[`PUBLIC_STATIC_DEMO.zip`](PUBLIC_STATIC_DEMO.zip)** |
| **Test & Verification Evidence** | **[`TEST_EVIDENCE.json`](TEST_EVIDENCE.json)** |

---

## What the Interactive Prototype Does (`https://sanjayj23.github.io/kuku-promo-lab/`)

1. **4-Step Guided Tour (`Next →` / `← Back` / `Skip Tour`):** Walks reviewers through the Ranked Promo Ledger, the interactive `▶ Play Cut` Screening Monitor, the Synchronized Teleprompter & Blind Cold-Viewer Audit, and the in-browser `.ZIP` / `.TXT` runner.
2. **Ranked Promo Ledger:** Displays `52` ranked promo cuts across `Post First` (`15`), `Good` (`21`), and `Multi-Cut` (`15`) tiers, showing exact `20–60s` timestamp windows (`E32 01:59.5–02:34.4`), proportional timeline bars, `[00–03s HOOK]` opening beats, and `[CUT POINT]` cliffhanger quotes.
3. **Interactive `▶ Play Cut` Screening Monitor:** Plays any selected promo cut beat-by-beat (`1×` / `2×` speed or `Space`bar play/pause) from the teal `00–03s Hook Zone` through escalation beats to the vermilion `Cut Point Cliffhanger`, synced with a clickable beat timeline, live script teleprompter, and 4-metric Cold-Viewer Audit (`0–3s Hook`, `Cliffhanger`, `Clarity`, `Tap Drive`).
4. **Run on Any New `.ZIP` or `.TXT` Screenplay Pack:** Drop any single episode (`.txt`) or multi-episode archive (`.zip`)—whether `1`, `10`, `50`, or `100+` episodes—in Tab **`02 / Run on Any .ZIP or .TXT`** to unpack, parse, extract promos in the browser, optionally call a live swappable Gemini/OpenAI endpoint, and export `promos.csv` / `promos.json`.

---

## How the 4-Stage Pipeline Works (`src/kuku_promo_lab/v8/`)

1. **Stage 0 — Line-ID Transcript Parser (`transcript.py`):**
   Parses `.zip` or folder screenplay packs of any episode count into numbered, millisecond-timed `speech`, `camera`, and `action` units (`L001..Lxxx`).
2. **Stage 1 — Story-Aware Proposer (`pipeline.py`):**
   Processes episodes in batches alongside a rolling story memory and selects candidate single-segment or multi-segment promo cuts by line ID (`start_line`, `end_line`), never guessing raw timestamps.
3. **Stage 2 — Deterministic Boundary Check & 1-Round Auto-Repair (`pipeline.py`):**
   Snaps line IDs to complete utterances (never cutting a spoken line mid-sentence), verifies `20–60s` total duration, `<= 3 segments`, and `<= 3.0s` speech-hook timing, and sends exact violation feedback for 1 repair pass (`10` cuts auto-repaired; `8` rejected).
4. **Stage 3 & 4 — Blind Cold-Viewer Critic & Ranker (`pipeline.py`, `report.py`):**
   Retimes each valid cut to `0.0s` and evaluates it in a fresh model call with **zero prior episode context**, shuffled blind alongside two naive baselines per episode (last-40s episode ending and random 40s window), ranking cuts into `post_first`, `good`, and `weak`.

---

## Quick Start (CLI & Swappable Model Endpoints)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock && .venv/bin/pip install --no-deps -e .
```

### 1. Run with Gemini Flash (`config/promos.gemini.json`)
```bash
export GEMINI_API_KEY=your_key_here
.venv/bin/kuku promos \
  --input /path/to/screenplays.zip \
  --out private/runs/new-run \
  --config config/promos.gemini.json
```

### 2. Swap to Any OpenAI-Compatible Endpoint or Model via Environment Variables
```bash
KUKU_BASE_URL=https://api.openai.com/v1 \
KUKU_MODEL=gpt-4.1-mini \
OPENAI_API_KEY=your_key_here \
.venv/bin/kuku promos \
  --input /path/to/screenplays.zip \
  --out private/runs/openai-run
```

### 3. Run Offline Against a Local `llama.cpp` Server (`config/promos.local.json`)
```bash
.venv/bin/kuku promos \
  --input /path/to/screenplays.zip \
  --out private/runs/local-run \
  --config config/promos.local.json
```

### 4. Run Unit & Verification Tests
```bash
.venv/bin/python -m unittest discover -s tests
```

---

## Repository Structure

- [`src/kuku_promo_lab/v8/`](src/kuku_promo_lab/v8/pipeline.py) — Core 4-stage promo finder (`pipeline.py`, `transcript.py`, `llm.py`, `report.py`, `schema.py`)
- [`config/`](config/) — Swappable model configurations (`promos.gemini.json`, `promos.openai.json`, `promos.local.json`)
- [`RAW_OUTPUTS_V8_50_EPISODES/`](RAW_OUTPUTS_V8_50_EPISODES/) — Full unedited 50-episode run outputs (`promos.json`, `promos.csv`, `report.html`, `quality.json`, `baselines.json`, `rejected.json`, `alternates.json`, `run_manifest.json`, and all raw `model_calls/*.json`)
- [`FINAL_SOLUTION.pdf`](FINAL_SOLUTION.pdf) — Two-page solution write-up (approach, AI workflow, failure cases, scaling to 500+ episodes & audio/video)
- [`scripts/build_apple_prototype.py`](scripts/build_apple_prototype.py) — Builds the static interactive Editorial Cutting Room & `Play Cut` prototype (`index.html`)
- [`scripts/make_solution_pdf_v8.py`](scripts/make_solution_pdf_v8.py) — Builds the two-page `FINAL_SOLUTION.pdf`
