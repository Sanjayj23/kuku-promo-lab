"""Unit tests for the V8 promo pipeline (transcript, cuts, rank, report, mocked end-to-end)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kuku_promo_lab.ingest import read_pack
from kuku_promo_lab.v8.cuts import Segment, check_candidate, clip_script, overlap_ratio
from kuku_promo_lab.v8.pipeline import PACK_LIMITS, run
from kuku_promo_lab.v8.rank import rank_promos
from kuku_promo_lab.v8.transcript import fmt_tc, load_episodes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic"


class V8PipelineTest(unittest.TestCase):
    def setUp(self):
        self.files, _ = read_pack(FIXTURES, PACK_LIMITS)
        self.docs = load_episodes(self.files)

    def test_transcript_numbering_and_timecodes(self):
        doc = self.docs[0]
        self.assertEqual(doc.lines[0].lid, "L001")
        self.assertIn("[L001 ", doc.render())
        self.assertEqual(fmt_tc(65_400), "01:05.4")

    def test_check_candidate_enforces_duration_hook_and_quote(self):
        doc = self.docs[0]
        first_speech = next(ln for ln in doc.lines if ln.is_speech)
        end_line = next(ln for ln in reversed(doc.lines) if 22_000 <= ln.end_ms - first_speech.start_ms <= 55_000)
        cand = {
            "segments": [{"start_line": first_speech.lid, "end_line": end_line.lid}],
            "hook_line": first_speech.lid,
            "hook_quote": first_speech.text.strip('"')[:20],
            "cliffhanger_line": end_line.lid,
            "cliffhanger_quote": "",
        }
        segs, problems, _ = check_candidate(doc, cand)
        self.assertEqual(problems, [])
        self.assertEqual(len(segs), 1)
        self.assertTrue(20_000 <= segs[0].duration_ms <= 60_000)

        # Too short (<20 s) is rejected
        short_cand = dict(cand, segments=[{"start_line": first_speech.lid, "end_line": first_speech.lid}])
        _, problems, _ = check_candidate(doc, short_cand)
        self.assertTrue(any("outside 20-60 s" in p for p in problems))

        # Fabricated quote is rejected
        bad_quote = dict(cand, hook_quote="this phrase never appears in the episode")
        _, problems, _ = check_candidate(doc, bad_quote)
        self.assertTrue(any("hook_quote" in p for p in problems))

    def test_ranking_deduplicates_overlapping_cuts_and_caps_per_episode(self):
        c1 = {
            "promo_id": "E01-a", "episode": 1, "proposer_rank": 1, "title": "Cut A",
            "segments": [Segment(1, 0, 35_000, "L001", "L010").__dict__], "duration_ms": 35_000,
            "flags": [], "spoiler_risk": "low",
            "critic": {"hook": 5, "cliffhanger": 5, "standalone": 4, "tap_to_watch": 5},
        }
        c2_dup = {
            "promo_id": "E01-b", "episode": 1, "proposer_rank": 2, "title": "Overlapping Cut B",
            "segments": [Segment(1, 2_000, 36_000, "L002", "L010").__dict__], "duration_ms": 34_000,
            "flags": [], "spoiler_risk": "low",
            "critic": {"hook": 4, "cliffhanger": 4, "standalone": 4, "tap_to_watch": 4},
        }
        self.assertGreater(overlap_ratio([Segment(**c1["segments"][0])], [Segment(**c2_dup["segments"][0])]), 0.5)
        ranked, alternates = rank_promos([c1, c2_dup], {"top_n": 10, "max_per_episode_in_ranking": 2})
        self.assertEqual([r["promo_id"] for r in ranked], ["E01-a"])
        self.assertEqual([a["promo_id"] for a in alternates], ["E01-b"])
        self.assertIn("overlaps", alternates[0]["alternate_reason"])

    def test_end_to_end_mocked_run_writes_all_outputs(self):
        doc = self.docs[0]
        s_line = next(ln for ln in doc.lines if ln.is_speech)
        e_line = next(ln for ln in reversed(doc.lines) if 22_000 <= ln.end_ms - s_line.start_ms <= 55_000)

        def fake_chat(self_client, role, system, user, *, validate=None, call_id=None, temperature=None):
            if role == "propose":
                reply = {
                    "episode_summary": "Synthetic test episode summary.",
                    "candidates": [{
                        "title": "Public confrontation",
                        "segments": [{"start_line": s_line.lid, "end_line": e_line.lid}],
                        "hook_line": s_line.lid,
                        "hook": "Immediate accusation in the opening line.",
                        "hook_quote": "",
                        "cliffhanger_line": e_line.lid,
                        "cliffhanger": "Unresolved standoff at the cut.",
                        "cliffhanger_quote": "",
                        "why_it_works": "Opens on conflict and cuts before the answer.",
                        "setup_needed": "none",
                        "spoiler_risk": "low",
                        "self_score": 8,
                    }],
                }
            else:
                n = user.count("=== CLIP ")
                reply = {
                    "reviews": [
                        {"clip": "ABCDEFGHIJ"[i], "first_3s": "Immediate confrontation", "hook": 4,
                         "understood": "Two rivals clash", "confusions": "none", "standalone": 4,
                         "open_question": "Who yields first?", "cliffhanger": 4, "tap_to_watch": 4,
                         "main_problem": "minor context gap"}
                        for i in range(n)
                    ]
                }
            if validate:
                validate(reply)
            return reply, {"origin": "mock"}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("kuku_promo_lab.v8.llm.ChatClient.chat_json", new=fake_chat), \
                 patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
                manifest = run(FIXTURES, tmp, episodes=[doc.number], log=lambda *_: None)
            self.assertEqual(manifest["failures"], [])
            for fname in ("promos.csv", "promos.json", "alternates.json", "rejected.json",
                          "quality.json", "run_manifest.json", "report.html"):
                self.assertTrue((Path(tmp) / fname).exists(), fname)


if __name__ == "__main__":
    unittest.main()

