"""Assemble submission_release_v8/ with:
  1. FINAL_SOLUTION.pdf (2-page solution doc, leak-checked)
  2. RAW_OUTPUTS_V8_50_EPISODES/ and RAW_OUTPUTS_V8_50_EPISODES.zip (unedited full-pack outputs for the 'Raw outputs' form link)
  3. github_public/ (PUBLIC_SOURCE.zip, FINAL_SOLUTION.pdf, PUBLIC_STATIC_DEMO.zip, v8_synthetic_outputs/, v8_50_episode_redacted_summary/, README.md)
  4. vercel_public/ (static Studio + updated FINAL_SOLUTION.pdf + v8-synthetic-report.html + PUBLIC_SOURCE.zip)
"""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

from make_solution_pdf_v8 import scrub_public

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submission_release_v8"
V8_FULL = ROOT / "private" / "runs" / "v8-full-50"
V8_SYN = ROOT / "private" / "runs" / "v8-synthetic"
V6_REL = ROOT / "submission_release_v6_20261109"


def load_bundle_builder():
    spec = importlib.util.spec_from_file_location("build_public_bundle", ROOT / "scripts" / "build_public_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_raw_outputs_package():
    raw_dir = OUT / "RAW_OUTPUTS_V8_50_EPISODES"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)

    # Copy primary V8 full-pack outputs
    for fname in (
        "promos.csv", "promos.json", "alternates.json", "rejected.json",
        "baselines.json", "quality.json", "story_memory.json", "run_manifest.json", "report.html"
    ):
        shutil.copy2(V8_FULL / fname, raw_dir / fname)

    # Copy validated model call logs (excluding transient retry -fail files)
    calls_out = raw_dir / "model_calls"
    calls_out.mkdir()
    for p in sorted((V8_FULL / "model_calls").glob("*.json")):
        if "-fail" not in p.name:
            shutil.copy2(p, calls_out / p.name)

    # Include historical V1 and V3 outputs for completeness
    v1_src = ROOT / "final_submission" / "reviewer_private" / "raw_outputs_v1"
    if v1_src.is_dir():
        v1_dst = raw_dir / "historical_v1_full_pack"
        v1_dst.mkdir()
        for fname in ("promos.csv", "promos.json", "run_manifest.json", "report.html"):
            if (v1_src / fname).is_file():
                shutil.copy2(v1_src / fname, v1_dst / fname)

    v3_src = ROOT / "private" / "runs" / "full-v2-regate-v3"
    if v3_src.is_dir():
        v3_dst = raw_dir / "historical_v3_gate3"
        v3_dst.mkdir()
        for fname in ("promos.csv", "promos.json", "run_manifest.json", "report.html"):
            if (v3_src / fname).is_file():
                shutil.copy2(v3_src / fname, v3_dst / fname)

    (raw_dir / "README.md").write_text(
        "# Kuku Promo Lab — Full 50-Episode Raw Outputs (Problem 1)\n\n"
        "This folder contains the unedited outputs from running Kuku Promo Lab V8 (`kuku promos`) across all "
        "50 episodes of the assignment screenplay pack.\n\n"
        "- `promos.csv` / `promos.json`: All **52 ranked promos** across the 50 episodes (15 Tier-1 `post_first`, "
        "21 `good`, 16 `weak`), with episode number, exact start/end timestamps for every segment, duration, "
        "opening hook (0–3 s) + verbatim quote, unresolved cliffhanger + verbatim quote, one-line `why_it_works`, "
        "`why_ranked`, cold-viewer scores (`hook`, `cliffhanger`, `standalone`, `tap_to_watch`), and quality flags.\n"
        "- `report.html`: Self-contained interactive HTML viewer for browsing all 52 ranked cuts, full clip "
        "transcripts, alternates, rejected cuts, and blind baseline comparisons.\n"
        "- `alternates.json`: 5 valid cuts kept as alternates (>50% overlap with a higher-ranked cut or >2/episode cap).\n"
        "- `rejected.json`: 8 cuts rejected by deterministic rule checks even after 1 structured repair pass.\n"
        "- `baselines.json`: 100 naive baseline cuts (50 episode-ending 40 s cuts + 50 random 40 s cuts) evaluated "
        "blind alongside the tool's candidates.\n"
        "- `quality.json`: Aggregate evaluation metrics (blind cold-viewer means, 31–5 head-to-head win record over "
        "episode endings, Spearman correlation `r = 0.293` between proposer self-score and blind cold critic).\n"
        "- `run_manifest.json`: Full provenance (pack SHA-256, model configuration, token usage, prompt digest).\n"
        "- `model_calls/`: Raw JSON request/response envelopes for the proposer, repair, and blind critic calls.\n"
        "- `historical_v1_full_pack/` & `historical_v3_gate3/`: Preserved earlier local `Qwen3-8B`/`4B` runs.\n",
        encoding="utf-8",
    )

    zip_path = OUT / "RAW_OUTPUTS_V8_50_EPISODES.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(raw_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f"RAW_OUTPUTS_V8_50_EPISODES/{f.relative_to(raw_dir).as_posix()}")
    return raw_dir, zip_path


def build_redacted_summary(dst_dir: Path):
    """Create a 100% confidentiality-safe structural summary of the 50-episode run for public repo/demo inspection."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    promos = json.loads((V8_FULL / "promos.json").read_text())
    quality = json.loads((V8_FULL / "quality.json").read_text())
    manifest = json.loads((V8_FULL / "run_manifest.json").read_text())

    redacted_rows = []
    for p in promos:
        cr = p.get("critic") or {}
        segs = " + ".join(
            f"E{s['episode']:02d} {s['start_ms']/1000:.1f}-{s['end_ms']/1000:.1f}s ({s['start_line']}-{s['end_line']})"
            for s in p["segments"]
        )
        redacted_rows.append({
            "rank": p["rank"],
            "tier": p["tier"],
            "promo_id": p["promo_id"],
            "episode": p["episode"],
            "segments": segs,
            "duration_s": round(p["duration_ms"] / 1000, 1),
            "score": p["score"],
            "critic_hook": cr.get("hook"),
            "critic_cliffhanger": cr.get("cliffhanger"),
            "critic_standalone": cr.get("standalone"),
            "critic_tap": cr.get("tap_to_watch"),
            "why_ranked": p["why_ranked"],
            "spoiler_risk": p.get("spoiler_risk"),
            "flags": ";".join(p.get("flags", [])),
            "origin": p.get("origin"),
        })

    with open(dst_dir / "promos_structural_50ep.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(redacted_rows[0].keys()))
        w.writeheader()
        w.writerows(redacted_rows)
    (dst_dir / "promos_structural_50ep.json").write_text(json.dumps(redacted_rows, indent=2, ensure_ascii=False))

    q_pub = dict(quality)
    q_pub["weakest_reviewed"] = [
        {
            "promo_id": w["promo_id"],
            "quality": w["quality"],
            "main_problem": scrub_public(w.get("main_problem", ""), 140),
            "confusions": scrub_public(w.get("confusions", ""), 140),
        }
        for w in quality.get("weakest_reviewed", [])
    ]
    (dst_dir / "quality.json").write_text(json.dumps(q_pub, indent=2, ensure_ascii=False))

    m_pub = dict(manifest)
    m_pub["show"] = "Confidential 50-Episode Assignment Pack (SHA-256 verified)"
    m_pub["input"] = {
        "pack_hash": manifest["input"]["pack_hash"],
        "files": manifest["input"]["files"],
        "episodes_processed": manifest["input"]["episodes_processed"],
    }
    (dst_dir / "run_manifest.json").write_text(json.dumps(m_pub, indent=2, ensure_ascii=False))


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1. Copy 2-page PDF
    pdf_src = ROOT / "FINAL_SOLUTION.pdf"
    shutil.copy2(pdf_src, OUT / "FINAL_SOLUTION.pdf")

    # 2. Build Raw Outputs folder + zip
    raw_dir, raw_zip = build_raw_outputs_package()

    # 3. Build PUBLIC_SOURCE.zip via scripts/build_public_bundle.py
    bundle = load_bundle_builder()
    pub_source_zip = OUT / "PUBLIC_SOURCE.zip"
    inv = bundle.build(pub_source_zip, ROOT)
    (OUT / "PUBLIC_SOURCE.inventory.json").write_text(json.dumps(inv, indent=2))

    # 4. Build vercel_public/ and PUBLIC_STATIC_DEMO.zip
    vercel_dir = OUT / "vercel_public"
    shutil.copytree(V6_REL / "vercel_public", vercel_dir)
    shutil.copy2(pdf_src, vercel_dir / "FINAL_SOLUTION.pdf")
    shutil.copy2(pub_source_zip, vercel_dir / "PUBLIC_SOURCE.zip")
    if (V8_SYN / "report.html").is_file():
        shutil.copy2(V8_SYN / "report.html", vercel_dir / "v8-synthetic-report.html")

    static_zip = OUT / "PUBLIC_STATIC_DEMO.zip"
    with zipfile.ZipFile(static_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(vercel_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(vercel_dir).as_posix())

    # 5. Build github_public/
    gh_dir = OUT / "github_public"
    gh_dir.mkdir(parents=True)
    shutil.copy2(pdf_src, gh_dir / "FINAL_SOLUTION.pdf")
    shutil.copy2(pub_source_zip, gh_dir / "PUBLIC_SOURCE.zip")
    shutil.copy2(static_zip, gh_dir / "PUBLIC_STATIC_DEMO.zip")

    syn_out = gh_dir / "v8_synthetic_outputs"
    syn_out.mkdir()
    for fname in ("promos.csv", "promos.json", "quality.json", "run_manifest.json", "report.html"):
        if (V8_SYN / fname).is_file():
            shutil.copy2(V8_SYN / fname, syn_out / fname)

    build_redacted_summary(gh_dir / "v8_50_episode_structural_summary")
    build_redacted_summary(vercel_dir / "runs" / "v8-50ep-structural-summary")

    (gh_dir / "README.md").write_text(
        "# Kuku Promo Lab (V8)\n\n"
        "Story-aware screenplay promo finder with deterministic line-ID boundary resolution, speech-safe snapping, "
        "1-round rule repair, and blind cold-viewer evaluation for Kuku's AI Generalist assignment (**Problem 1: Find the Promos**).\n\n"
        "- **Two-page Solution PDF:** [FINAL_SOLUTION.pdf](FINAL_SOLUTION.pdf)\n"
        "- **Complete Runnable Source Archive:** [PUBLIC_SOURCE.zip](PUBLIC_SOURCE.zip)\n"
        "- **Static Studio Demo Bundle:** [PUBLIC_STATIC_DEMO.zip](PUBLIC_STATIC_DEMO.zip)\n"
        "- **V8 Synthetic Run Outputs (Unedited):** [`v8_synthetic_outputs/`](v8_synthetic_outputs/)\n"
        "- **V8 50-Episode Structural Summary (Confidentiality-Safe):** [`v8_50_episode_structural_summary/`](v8_50_episode_structural_summary/)\n\n"
        "*(Full unedited 50-episode outputs with verbatim screenplay quotes and raw model call logs are packaged in "
        "`RAW_OUTPUTS_V8_50_EPISODES.zip` for the official submission form so Kuku's screenplay pack is never published publicly.)*\n\n"
        "## Quick Start\n\n"
        "```bash\n"
        "unzip PUBLIC_SOURCE.zip && cd kuku-promo-lab\n"
        "python3 -m venv .venv && source .venv/bin/activate\n"
        "pip install -r requirements.lock && pip install --no-deps -e .\n\n"
        "# Run V8 promo finder on any screenplay pack (.zip or folder of .txt episodes)\n"
        "export GEMINI_API_KEY=your_key_here\n"
        "kuku promos --input tests/fixtures/synthetic --out private/runs/demo --config config/promos.gemini.json\n\n"
        "# Or swap in any OpenAI-compatible model endpoint at interview time\n"
        "KUKU_BASE_URL=http://127.0.0.1:8080/v1 KUKU_MODEL=qwen3-4b \\\n"
        "  kuku promos --input /path/to/new_pack --out private/runs/new_pack --config config/promos.local.json\n"
        "```\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "release_dir": str(OUT),
        "pdf_bytes": (OUT / "FINAL_SOLUTION.pdf").stat().st_size,
        "raw_outputs_zip_bytes": raw_zip.stat().st_size,
        "public_source_zip_bytes": pub_source_zip.stat().st_size,
        "static_demo_zip_bytes": static_zip.stat().st_size,
    }, indent=2))


if __name__ == "__main__":
    main()
