# Tools and limits disclosure

Implementation and debugging were performed with the Codex coding assistant. The user requested GPT-6.1 Sol with high reasoning; that product-side configuration was not independently verified through an API. The supplied architecture/handoff and ideation documents were read as design inputs; their stated authorship was not independently authenticated. Local Qwen3-4B Q4_K_M through llama.cpp was used during development on an 8 GB Apple M1. The user's private Kaggle configuration uses official Qwen3-8B Q4_K_M on a T4, with the same model for analyst, restricted reviewer and direct baseline. Actual run manifests record which model, runtime, weight digest, calls and cache origins produced each artifact. Python handles ingestion, validation, search, ranking, exports and tests. ReportLab creates the short PDF and Poppler renders it for inspection.

A live synthetic test caught a real failure: the local model returned a 2-second cut despite a 20–60 second instruction, and repeated that mistake after repair. Code rejected it. The analyst response schema was changed to constrain source boundary pairs to structurally valid durations. Earlier raw runs are retained privately. A separate initial response repeated itself until token truncation; bounded output and revised prompts address that failure without pretending the response succeeded.

Passing timing/reference checks does not establish editorial quality. Real human ratings, finished-video alignment and audience outcomes are not inferred from model scores.

Cloud setup exposed separate infrastructure failures: Kaggle's ensurepip failed, its source CUDA build lacked a driver-link target, and compiling all CUDA kernels on two CPUs was slow. A separate pinned-package directory and checksum-verified official CUDA release resolved setup. A sidebar dataset attachment was not initially readable in the running VM; the official Kaggle client made the authorized private dataset available. These are setup findings, not editorial-quality measurements.

The first full 8B run technically completed but rejected all 47 reviewed cuts, each with essential context marked missing. Source inspection found demands for exact identities/backstory even when local roles were understandable, a reversed proposal explanation and questions already answered by the dialogue. A first prompt clarification also incorrectly passed an authored ambiguous control, so it was not used for a production rerun. A balanced revision requires supported local roles, immediate conflict and explicit stakes, while distinguishing unknown names from blocking setup. It passed an authored rescue dilemma and rejected ambiguous pronouns in genuine fresh calls. The production candidate pool, numeric gates, weights and source boundaries were kept fixed for the revised review run. These controls are development checks, not human evidence or an untouched holdout study. Every earlier raw response is retained privately.

Final source inspection still found weak one-word hook judgments, answered questions and an invented bell. These are disclosed in the private AI source audit; raw scores were not edited into favorable results. Final engineering checks also corrected cached doctor success, replay's unnecessary dependence on the original cache directory, and an equal-weight diagnostic that had changed tie handling as well as weights. The corrected diagnostic and portable zero-call replay are separate derived artifacts; the original run remains intact.

## Verified improvement pass
Codex checked the supplied independent review against the current functions,
added versioned post-processing and reran offline validation. Some review
claims were overstated: V2 replay and an end-to-end V2 test already existed.
The review's model authorship was not verified. The live control harness is
implemented and mock-tested. The local service refused the doctor request,
so no new live controls or fresh synthetic inference are claimed.

## V8 (`kuku promos`) pass
Gemini added the provider-agnostic `kuku promos` pipeline (`src/kuku_promo_lab/v8/`),
which accepts any OpenAI-compatible `/chat/completions` endpoint (configured for
Gemini Flash in `config/promos.gemini.json` and local `llama.cpp` Qwen3-4B in
`config/promos.local.json`). Instead of free-form timestamps or brittle lexical
gates, the proposer selects cuts by line IDs (`L0xx–L0yy`), deterministic code
snaps boundaries to complete speech and verifies 20–60 s duration, a 0–3 s hook,
and verbatim quotes (with a 1-round repair loop), and a blind cold-viewer critic
scores the `0.0 s`-retimed clips shuffled alongside naive episode-ending and
random-window baselines. Across all 50 episodes, V8 produced 52 ranked promos
(15 Tier-1 `post_first`), 5 deduplicated alternates, and 8 rejected cuts (10
repaired in 1 round), beating naive episode endings 31–5 (with 7 ties) in blind
evaluation.

## Studio and submission release (Claude)

Claude (Anthropic) built the read-only Studio (`studio.py`, `studio_demo.py`, `ui.py`, `studio/`),
its tests, the two-page solution PDF, the design system, the diagrams and the walkthrough kit.
It did not change the parser, pipeline, prompts, schemas, gates, ranking or any historical run.
No new model inference was run in this release, because the environment had no GPU or model server.

Mistakes made in this release, and how they were caught:

- The first Studio hero illustration carried made-up scores that could be mistaken for data.
  Removed on visual review; the illustration is now labelled "schematic only".
- The Studio rounded the score 66.25 to 66.3 while the PDF printed 66.2. Both now print exact values.
- The first PDF embedded only one weight per font family (all static instances shared an internal
  name), so bold text silently rendered as regular. Caught by `pdffonts`; the instances were renamed.
- Contrast ratios in the design-system note were first written from memory; recomputed with the
  WCAG formula and corrected, and the print accent inks were darkened to pass 4.5:1.

**Confidentiality fix in this release:** a confidentiality scan of the public tree against the
confidential pack found one authored V4 control (`tests/fixtures/editorial_controls_v4.json`,
case `legal_argument_not_ruling`) reusing a line taken verbatim from the show. In the public source it
was replaced by a neutral paraphrase with the same function. The historical matched experiments used
the original wording; the original fixture remains in the private archive. No test depends on this file.

## V5 source check release (Claude)

Claude added the deterministic V5 source check (`source_check.py`, `kuku source-check`), its tests, the
rebuilt public bundle generator, and corrected documentation. No model inference was run: the environment
had no GPU, huggingface.co was blocked by network policy, and there was no Kaggle credential.

Mistakes made and caught in this release:

- The quote locator first matched the one-word quote "When?" inside a different line (a different, longer line). Found by reading all 15 outside-cut matches against the script; one- and two-word
  quotes now require an exact whole-line match.
- The first version of the rule tests quoted real screenplay lines. Caught before packaging; replaced with
  synthetic paraphrases, and the bundle builder can now scan against the private pack (`--private-pack`).
- Earlier documentation said any OpenAI-compatible endpoint, or a paid API, would work. The client accepts
  only loopback HTTP; the wording now says so.


## V6 submission corrections (Codex)

Codex removed token-overlap quote matching after an authored reversal probe was
incorrectly grounded. Only normalized exact lines or contiguous fragments verify
a quotation. Explicit acceptance and evidential confirmations are covered by
regressions; speech-act and word-overlap warnings remain advisory, not semantic
proof. Five saved-run audits and the synthetic demo were regenerated with zero
model calls in new directories. No human ratings were generated. Historical raw
model outputs, original prompts and original PDFs remain preserved.
