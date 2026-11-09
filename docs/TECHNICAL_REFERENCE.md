# Kuku Promo Lab — Technical Reference

> **Production Pipeline (`kuku promos` / V8):** For the main V8 architecture (`src/kuku_promo_lab/v8/`), CLI usage, 4-stage pipeline design, and 50-episode evaluation results, see the root [`README.md`](../README.md) and [`FINAL_SOLUTION.pdf`](../FINAL_SOLUTION.pdf). The sections below preserve the detailed technical reference for the local `llama.cpp` V1–V6 subcommands (`kuku run`, `kuku replay`, `kuku source-check`, `kuku studio`).

A tool that reads timestamped screenplay episodes, proposes continuous or 2-part 20–60 second cuts, snaps boundaries to complete speech lines, and evaluates each cut in a fresh blind dialogue-only model request. It exports ranked eligible recommendations and preserves weak candidates, alternatives, failures, and provenance.

Script coordinates are not verified video timestamps. Editorial scores are model judgments, not audience or conversion predictions. Human review is required.

## V4 development status

V4 is an opt-in, bounded development path. `config/default.json` and the historical
V1/V2/V3 prompts remain unchanged. `config/v4.json` retains timing-feasible legacy
windows, refills the menu, permits up to two proposals, deduplicates overlapping
event proposals provisionally, and budgets 24 initial reviews plus 24 extra cuts.
Its live episode limit is 12; the development inference pilot used only two.

Versioned prompts in `prompts/v4/` request participant, entity, antecedent and
question/answer evidence. Citation validation proves source provenance, not the
truth of the model's interpretation. Unknown answers remain review-needed;
answered or contradicted cliffhangers cannot qualify. The first-three-second
review receives only complete speech contained in that interval.

V4 has not demonstrated an editorial advantage over direct selection. The
development pilot still encounters semantic and evidence-contract failures.
Automated tests do not substitute for independent source auditing, cold human
ratings or a separate unseen-story evaluation. No full 50-episode V4 inference
run or replacement submission PDF is included. Larger-model adoption requires
source-audited evidence, not a higher acceptance count.

## Install

Use Python 3.11 or later (3.12.14 was tested). The core needs only the Python standard library and `jsonschema`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m unittest discover -s tests -v
```

The lock includes optional PDF-authoring packages; they are not imported by the app. `pip install -e .` alone installs the core dependency. Run from the repository root so relative cache/output paths are predictable.

## Start local inference

Use a local OpenAI-compatible chat-completion server with JSON Schema support, such as [llama.cpp](https://github.com/ggml-org/llama.cpp/tree/master/tools/server). The tested setup is llama.cpp **b12114**, commit **b393d73bb**, with official [Qwen3-4B Q4_K_M weights](https://huggingface.co/Qwen/Qwen3-4B-GGUF), revision `bc640142c66e1fdd12af0bd68f40445458f3869b`. Weight SHA-256: `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`.

Download a runtime for your platform and the model privately. This tested command used an 8 GB Apple M1; other hardware needs its own smoke test:

```sh
llama-server -m /path/to/Qwen3-4B-Q4_K_M.gguf --alias qwen3-4b \
  --host 127.0.0.1 --port 8080 --ctx-size 16384 --parallel 1 \
  --gpu-layers 99 --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 \
  --reasoning off --no-context-shift --cache-ram 0 --ctx-checkpoints 0 --no-webui
```

Copy `config/default.json` to a private configuration, set actual model/runtime identities and paths, and measure connectivity:

```sh
.venv/bin/kuku doctor --config private/local-config.json --out private/doctor-new
```

Only loopback HTTP is accepted. There is no automatic remote source upload, paid API assumption, telemetry or credential handling. Keep inference concurrency at one on small machines. A chat subscription does not provide an API key.
Each `doctor` invocation uses its own empty cache, so a previous success cannot
hide a stopped server. Analysis and review still use the normal audited cache.

## Run the synthetic pack

```sh
.venv/bin/kuku run --input tests/fixtures/synthetic --out private/run-v2-demo --config config/default.json
.venv/bin/kuku validate --input tests/fixtures/synthetic --run private/run-v2-demo
```

## Replay with the model stopped

```sh
.venv/bin/kuku replay --input tests/fixtures/synthetic --run tests/fixtures/synthetic_replay --out private/demo-replay
```

The bundled fixture uses genuine saved synthetic responses. Replay needs the original
source, prompt and inference identities. It reports zero fresh requests.

## Analyze and review

```sh
.venv/bin/kuku run --input /path/to/private-scripts.zip \
  --out private/runs/my-run --config private/local-config.json
.venv/bin/kuku validate --input /path/to/private-scripts.zip --run private/runs/my-run
.venv/bin/kuku demo-select --run private/runs/my-run
.venv/bin/kuku evaluate --run private/runs/my-run --out private/evaluation/my-run
.venv/bin/python scripts/prepare_blind_openers.py \
  --pairs private/evaluation/my-run/blind_pairs.json \
  --out private/evaluation/my-run/blind_openers.json
```

For a larger GPU, the optional Kaggle helper keeps inference inside a private
Kaggle VM. Create an allowlisted source archive, then build a generic notebook
and a separate private input transport:

```sh
.venv/bin/python scripts/build_public_bundle.py --out dist/kaggle-source-new.zip
.venv/bin/python scripts/prepare_kaggle.py --source dist/kaggle-source-new.zip \
  --input /path/to/private-scripts.zip --notebook notebooks/Kuku_Kaggle_new.ipynb \
  --transport private/cloud-input/input-new.zip --dataset-handle owner/private-input-slug
```

Import the notebook into a **private** Kaggle notebook. Upload the transport as
a separate **private** dataset and attach it; privacy must be set on both.
The configured dataset handle must match the uploaded private dataset. The
notebook calls the official preinstalled Kaggle client to make it readable in
the running VM; a sidebar attachment alone may not mount it immediately.
Enable a T4 GPU and Internet for public software/model downloads. The notebook
uses official Qwen3-8B Q4_K_M weights at revision
`7c41481f57cb95916b40956ab2f0b139b296d974`, SHA-256
`d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`,
and uses checksum-verified official llama.cpp b12114 CUDA 12.8 binaries. It verifies the original ZIP bytes,
uses a separate pinned-package directory, records genuine calls, and offers
full-run validation, offline replay, a fresh-cache synthetic run and a private
results ZIP. It uses GPU 0; the second T4 is not needed for this model. A larger
GPU does not guarantee better editorial judgments. Phone verification or other
account checks must be completed by the account owner.

Defaults use one proposal per episode, a 48-window base menu, 1,400 output tokens and a 360-second request timeout for the tested small machine. V2 allows 462 model attempts, including retries. For 50 episodes and 12 families of at most five cuts, a cold run can require up to 230 first attempts before opener cache hits and skipped empty slices. Increasing proposal count may require a larger output budget.

Every output directory must be new. Original raw runs are never overwritten. A failed episode is explicit; a valid zero-proposal episode is different from failed inference. Exit codes: **0** technically complete processing, **2** invalid input/configuration/validation, **3** unavailable doctor/demo inference result, **4** partial/failed pipeline. A complete run may have no eligible recommendations.

For a browser interface with ZIP upload, progress, review cards, alternative cuts and JSON/CSV exports:

```sh
.venv/bin/kuku serve --config private/local-config.json --port 8765
```

Open [the local interface](http://127.0.0.1:8765). It binds only to loopback. New runs are saved in `private/web-runs/`. `report.html` can also be opened directly from any completed run. It shows the actual restricted packet, actual saved review and separately labeled original source. There is no video player or simulated human approval button.

## Replay and baselines

```sh
.venv/bin/kuku replay --input /path/to/private-scripts.zip \
  --run private/runs/my-run --out private/runs/my-replay
.venv/bin/kuku baseline --mode random --input /path/to/private-scripts.zip \
  --out private/baselines/random --config private/local-config.json
.venv/bin/kuku baseline --mode direct --input /path/to/private-scripts.zip \
  --out private/baselines/direct --config private/local-config.json
```

Replay uses genuine saved validated responses and makes no inference requests. It requires the same source, prompts, schemas, settings and model identity. Fresh inference is not guaranteed deterministic. Cache keys include role, allowed payload, prompt, schema and inference identities; analyst keys include source hashes. A fresh test can use a new `cache_dir`. Output directories alone are never used as inference caches.
Portable replay does not create or require the original machine's cache path.
The equal-weight diagnostic retains the primary tie policy, and evaluation
reports identical A/B packets separately from differing comparisons.

Every proposed run already records both baselines: seeded random safe intervals for every episode, and competent direct selection from the same actual story-aware analyst calls, without search or restricted review. Direct baseline cost is shared, not falsely counted as another full inference run. Its story-aware scores are kept separate from eligible restricted-reviewed exports. Human A/B review is pending until authorized people complete the blank worksheet.

## Compatible input

Supply a ZIP or folder containing one UTF-8 `.txt` per episode, following the original synthetic examples in `tests/fixtures/synthetic/`. These are authored science-fiction fixtures, not real footage measurements.

- Required: `EPISODE NUMBER: Episode N`; one or more `SCENE NN` sections; typed timed blocks.
- Optional: `EPISODE LOGLINE`, `CONTINUITY NOTES`, `TITLE`.
- Scene headers: `Scene heading`, `Scene start`, `Scene end`; case and whitespace variations accepted.
- Block: `**CAMERA:**`, `**ACTION:**`, `**SOUND FX:**`, or `**SPEAKER (tag):**`; then `Start: (MM:SS.ff) | End: (MM:SS.ff)` and text.
- `HH:MM:SS.fff` and up to millisecond precision are supported. No rounding or word-rate timing is used.
- If all scene times are omitted, block extent is the documented fallback; partial declarations fail.
- Overlapping speech is preserved. Boundaries inside any speech track are unsafe. Internal scene overruns warn; blocks beyond the declared episode bound fail.
- Unsupported material, malformed timing, duplicate episode IDs, binary/invalid UTF-8, traversal, symlinks, encrypted ZIPs and size/compression limits produce actionable errors.

## Measured runtime and limitations

On the real 50-episode pack, V1 took 47.7 minutes cold on a T4 under an earlier
rubric. The 18.1-minute V1 warm run reused 50 analyst responses and performed
47 fresh cut reviews. These are historical measurements. Full V2 fresh inference
depends on hardware, input length and abstention. Local model servers are
configurable. Hosted API integration requires separate work.

Hooks are script dialogue judgments, and entity/role checks remain model
judgments even when all references resolve. Uncertain and visual-led prospects
are in the family log for human review. The ranked list contains only tier A
suggestions. Score ties are visible and the human posting order is empty.
No demonstrated advantage over the direct baseline. Blind comparisons are
prepared and human ratings are pending.

## How selection works

1. Parse every scene/block with stable IDs, source hashes, exact text and line spans; use integer milliseconds and half-open intervals.
2. Give the analyst complete episode evidence and source metadata from the current and adjacent episodes. No model-generated global outline is used in this build.
3. Enumerate safe dialogue-boundary windows and evenly sample at most 48 as a bounded base menu (raw safe boundaries are a fallback when no dialogue windows exist). Constrain analyst output to one of these genuine 20–60 second pairs and evidence fully inside it. The model reads the complete episode and supplies the editorial choice and provisional judgments. The cap can miss good events; it was added after a live small-model test repeatedly cited outside its selected cut.
4. Keep up to five neighboring cuts per shortlisted family: base, prior/next safe dialogue start, prior/next safe dialogue end, within ten seconds. All pruning is recorded. Defaults shortlist five families; up to three proposals per episode are supported. The measured small-machine configuration uses one proposal per episode.
5. Construct one anonymous dialogue-only packet per cut and a separate opener payload. Hide title, episode/file, raw speaker names, emotion/camera/action/sound, metadata, analyst rationale, future material and adjacent cuts. Voice grouping follows local labels and is not identity recognition. Preserve names actually spoken. No word alignment is guessed across the three-second mark.
6. A fresh stateless model request returns interpretation, missing context, its own unanswered question and six cited scores. One bounded repair is permitted. Missing/invalid/unknown reviews cannot qualify. Exact duplicate strings in citation/context set fields are removed before validation, with an explicit normalization audit and the original raw response retained; ID values, text, scores and boundaries are never rewritten.
   The rubric separates missing setup from an unanswered outcome. Local roles,
   the immediate problem and a concrete stake must be supported; exact names
   alone are unnecessary. Vague fragments do not qualify as specific hooks.
7. Gate hook >=2, cliffhanger >=2, comprehension >=3, coherence >=2, boundary >=2, all six scores known/cited and no essential missing context. Rank `(20H+25C+25S+10E+10N+10B)/4`; use eligibility, score, comprehension, cliffhanger, hook, shorter duration and ID for ties. Flag differences below five points.
8. In V2, require an isolated opener verdict and a separate check of the proposed
   ending question. Verify quoted substrings, cited IDs and voice IDs. Flag
   semantic uncertainty and evidence too far from the final beat. Retain visual
   prospects separately when timed action overlaps the opening. V1 archived
   manifests remain readable for validation and replay.
9. Suppress exact and near duplicates using same-episode interval IoU >=0.70 or dialogue five-gram Jaccard >=0.80. Return fewer than ten when warranted. Independently re-read source for validation.

The information barrier is enforced in code and tested with metadata canaries. It does not remove pretrained model knowledge or prove that cited claims are true. Search is local and can miss better combined changes. Visual-led promos, word-aligned audio, montage assembly, ad-policy approval, human preference learning and conversion measurement are outside scope.

## Output and confidentiality

`run_manifest.json`, `coverage.json`, `validation.json`, `promos.json`, `promos.csv`, `provisional.json`, `candidate_families.jsonl`, `rejections.jsonl`, `failures.jsonl`, `checksums.sha256`, `report.html`, actual model logs and baseline files are retained. The manifest records configuration/code/prompt/schema/model identities, actual usage, timing, cache/retry origin, per-episode outcomes and completion state. Baseline and provisional outputs never masquerade as restricted-reviewed promos.

All real input, outputs, logs, caches and human packets belong under ignored `private/`. They can reconstruct confidential scripts. Do not publish them. Use `scripts/build_public_bundle.py` to create an allowlisted generic source ZIP, then inspect its inventory. Submission-only raw outputs and the solution PDF must be shared only through the intended assignment review route. Publishing/submission is a separate user action.

## No-model/manual mode

Set `provider` to `none` to exercise honest failure handling and obtain structural/random outputs; the proposed run is partial, with no fabricated semantic recommendations. For `manual`, requests are written to `manual_dir/<key>.request.json`. Obtain a genuine model response yourself and save `<key>.response.json` with `model`, `provenance`, and the exact `raw_text` string; rerun to a new output directory. This loses automation and is not equivalent to the tested local workflow. Mocks exist only in temporary unit tests.

For errors, inspect `failures.jsonl` and private model logs. Do not increase the context limit by silently truncating a script. A different model/runtime may not support the generated ID-pair JSON Schema; run `doctor` and a synthetic pack first.

### V2 tag policy

CAMERA, ACTION, SOUND FX (including SFX), LIGHTING, MUSIC, VFX, TRANSITION, TEXT,
SUPER, TITLE and INSERT are reserved non-dialogue labels, including when tagged.
A fully quoted line in the compatible format identifies a speaker label within
the pack. Unquoted text under a tagged label is dialogue only if that speaker
is established by a quoted line elsewhere in the pack. Unknown tagged labels
are preserved as ambiguous_tag, listed in validation.json, and excluded from
review packets. Review candidates with overlapping ambiguous material manually.
This policy is a format convention. Normalize other screenplay formats first.

### Rechecking a verified historical run

`kuku run --reuse-v1 V1_DIRECTORY ...` matches a completed V1 run to the exact
same source pack and keeps its search limits. The analyst and original full-cut
review responses are retrieved by exact request key and validated again. V2
opener and ending checks use the configured model and record fresh requests or
their own cache hits separately. Use an empty cache directory to measure fresh
editorial checks. Historic records may contain proprietary script data and
belong on the same private machine as the source pack.


## V2 measured status

The complete traversal of the 50-episode pack ended with partial status because
13 ending checks failed evidence validation. The run reused 97 historical Qwen
8B analyst/reviewer records and made 93 new Qwen 4B attempts, plus 13 opener
cache hits. It took 50.8 minutes on the M1 while a separate synthetic job shared
the server. Two suggestions were selected from four tier-A variant verdicts.
All 157 source interval records validate. Human review remains pending.
The entity checker still accepts an invented object in a flagged historical
candidate, and one selected question poorly matches its closing beat.

A separate fresh two-episode V2 synthetic run took 27.7 minutes with 19 attempts,
zero cache hits and one failed full review. It returned zero recommendations.
The supplied synthetic replay fixture comes from the earlier genuine Qwen 8B
synthetic run and reproduces two saved suggestions without a model server.
It exercises the V1 compatibility path. Live evaluation of the eight frozen
V2 semantic controls remains pending.

Create public release links only for generic code and the aggregate case study.
Keep source-derived results in the authorized assignment-review channel.

## Gate 3 and reproducible regating

Gate 3 is deterministic post-processing of the unchanged model responses.
It adds lexical grounding flags, not semantic verification. Short openers,
partial-word quote matches, unsupported question terms, unrelated entity
quotes, weak role evidence and speech after the cited ending require review.
Paraphrases and synonyms can create false flags. Thresholds and stopwords
were copied from the review specification without fitting private outputs.
Failed comprehension/coherence thresholds and missing essential setup exclude
a cut. Unknown or uncited judgments remain in the review queue.

Archived manifests without editorial_gate_version use gate 2. The gate-2
function body, prompts, inference settings and request keys are preserved.
A gate override applies only to V2 replay and never changes model requests.

```sh
.venv/bin/kuku replay --input PACK --run V2_RUN --out private/runs/regate-v3 --editorial-gate-version 3
.venv/bin/kuku validate --input PACK --run private/runs/regate-v3
.venv/bin/kuku replay --input tests/fixtures/synthetic --run tests/fixtures/synthetic_replay_v2 --out private/replay-v2-new
.venv/bin/kuku validate --input tests/fixtures/synthetic --run private/replay-v2-new
.venv/bin/python scripts/structural_hookability.py --input PACK --run V2_RUN --out private/hookability.json
.venv/bin/python scripts/run_editorial_controls.py --config private/local-config.json --repeats 3 --out private/controls-new
```

The V2 synthetic fixture contains genuine saved Qwen3-4B inference from
6 November 2026. Its original run was partial. The gate-3 replay makes zero
model calls. A new live gate-3 synthetic run and repeated controls have not
been performed because the local server is unavailable. Authored controls
were written after observed failures and are not a holdout or human study.

On the saved full V2 run, gate 3 changes A=4/B=17/Excluded=26 to
A=0/B=16/Excluded=31 and the ranked count from 2 to 0. All 157 intervals still
validate. The 13 failed ending checks remain failures. Both earlier finalists
now require review. This measures conservative abstention under lexical
checks and establishes no advertising improvement.

Every new gate-3 recommendation exposes boundary_decision in JSON, CSV and
the report. It identifies the deciding ordering factor and explicitly labels
choices made only by duration/ID tie-breaks.

Offline analysis found 5 of 50 base families structurally unhookable within
the current variants, including 2 of the 12 reviewed families. All 12 selected
families shared a provisional score with another base. At the selection
cutoff, 2 of 3 tied families were selected by family ID. Historical shortlist
and variant search are unchanged.
