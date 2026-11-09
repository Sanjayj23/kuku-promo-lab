"""Write every output of a V8 run: CSV/JSON for editors, raw rejects, quality metrics and an Apple-minimal HTML review page."""
from __future__ import annotations

import csv
import html
import json
import statistics
from collections import Counter
from pathlib import Path

from .cuts import Segment, clip_script
from .rank import quality
from .transcript import fmt_tc


def seg_text(segments):
    return " + ".join(f"E{s['episode']:02d} {fmt_tc(s['start_ms'])}–{fmt_tc(s['end_ms'])}" for s in segments)


def public_row(c):
    cr = c.get("critic") or {}
    return {
        "rank": c.get("rank", ""), "tier": c.get("tier", ""), "promo_id": c["promo_id"], "episode": c["episode"],
        "segments": seg_text(c["segments"]),
        "start": fmt_tc(c["segments"][0]["start_ms"]), "end": fmt_tc(c["segments"][-1]["end_ms"]),
        "duration_s": round(c["duration_ms"] / 1000, 1), "title": c.get("title", ""),
        "hook": c.get("hook", ""), "hook_quote": c.get("hook_quote", ""),
        "cliffhanger": c.get("cliffhanger", ""), "cliffhanger_quote": c.get("cliffhanger_quote", ""),
        "why_it_works": c.get("why_it_works", ""), "why_ranked": c.get("why_ranked", ""),
        "score": c.get("score", ""), "critic_hook": cr.get("hook", ""), "critic_cliffhanger": cr.get("cliffhanger", ""),
        "critic_standalone": cr.get("standalone", ""), "critic_tap": cr.get("tap_to_watch", ""),
        "critic_open_question": cr.get("open_question", ""), "critic_main_problem": cr.get("main_problem", ""),
        "spoiler_risk": c.get("spoiler_risk", ""), "setup_needed": c.get("setup_needed", ""),
        "flags": ";".join(c.get("flags", [])), "origin": c.get("origin", ""),
    }


def spearman(xs, ys):
    if len(xs) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    try:
        return round(statistics.correlation(rx, ry), 3)
    except statistics.StatisticsError:
        return None


def quality_report(ranked, alternates, rejected, baselines):
    cands = ranked + alternates
    reviewed = [c for c in cands if c.get("critic")]

    def mean(rows, key):
        vals = [float(r["critic"][key]) for r in rows if r.get("critic")]
        return round(statistics.mean(vals), 2) if vals else None

    groups = {"tool_best_per_episode": [], "tool_all": reviewed,
              "baseline_ending": [b for b in baselines if b["origin"] == "baseline_ending" and b.get("critic")],
              "baseline_random": [b for b in baselines if b["origin"] == "baseline_random" and b.get("critic")]}
    best = {}
    for c in reviewed:
        if c["episode"] not in best or quality(c["critic"]) > quality(best[c["episode"]]["critic"]):
            best[c["episode"]] = c
    groups["tool_best_per_episode"] = list(best.values())
    table = {name: {"n": len(rows), **{k: mean(rows, k) for k in ("hook", "cliffhanger", "standalone", "tap_to_watch")},
                    "quality_0_100": round(statistics.mean([quality(r["critic"]) for r in rows]), 1) if rows else None}
             for name, rows in groups.items()}
    duel = Counter()
    for b in groups["baseline_ending"]:
        mine = best.get(b["episode"])
        if mine:
            q1, q2 = quality(mine["critic"]), quality(b["critic"])
            duel["tool_wins" if q1 > q2 else "baseline_wins" if q2 > q1 else "ties"] += 1
    rand_duel = Counter()
    for b in groups["baseline_random"]:
        mine = best.get(b["episode"])
        if mine:
            q1, q2 = quality(mine["critic"]), quality(b["critic"])
            rand_duel["tool_wins" if q1 > q2 else "baseline_wins" if q2 > q1 else "ties"] += 1
    pairs = [(float(c["self_score"]), quality(c["critic"])) for c in reviewed
             if isinstance(c.get("self_score"), (int, float))]
    reasons = Counter()
    for r in rejected:
        for p in r.get("repair_problems") or r["problems"]:
            reasons[p.split(" is ")[0].split(" starts ")[0][:60]] += 1
    weakest = sorted(reviewed, key=lambda c: quality(c["critic"]))[:8]
    return {
        "candidates_valid": len(cands), "candidates_rejected_after_repair": len(rejected),
        "repaired_after_rule_check": sum("repaired_after_rule_check" in c["flags"] for c in cands),
        "rule_checks_on_ranked": "every ranked promo passed: 20-60 s, hook line starts in first 3 s, "
                                 "cliffhanger line inside the cut, exact quotes, no half-cut speech",
        "flags_on_ranked": dict(Counter(f for c in ranked for f in c["flags"])),
        "tiers": dict(Counter(c.get("tier") for c in ranked)),
        "blind_critic_means": table,
        "tool_best_vs_episode_ending_baseline": dict(duel),
        "tool_best_vs_random_window_baseline": dict(rand_duel),
        "proposer_self_score_vs_critic_spearman": spearman([p[0] for p in pairs], [p[1] for p in pairs]),
        "rejection_reasons": dict(reasons.most_common()),
        "common_critic_problems": Counter(str((c.get("critic") or {}).get("main_problem", "")).lower()
                                          for c in reviewed).most_common(12),
        "weakest_reviewed": [{"promo_id": c["promo_id"], "quality": quality(c["critic"]),
                              "main_problem": c["critic"].get("main_problem"),
                              "confusions": c["critic"].get("confusions")} for c in weakest],
    }


def write_outputs(out, manifest, ranked, alternates, rejected, baselines, summaries, by_num):
    out = Path(out)
    dump = lambda name, obj: (out / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    rows = [public_row(c) for c in ranked]
    with open(out / "promos.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["rank"])
        writer.writeheader()
        writer.writerows(rows)
    dump("promos.json", ranked)
    dump("alternates.json", alternates)
    dump("rejected.json", rejected)
    dump("baselines.json", baselines)
    dump("story_memory.json", summaries)
    q = quality_report(ranked, alternates, rejected, baselines)
    dump("quality.json", q)
    dump("run_manifest.json", manifest)
    (out / "report.html").write_text(render_html(manifest, ranked, alternates, rejected, q, by_num), encoding="utf-8")


CSS = """
:root {
  --bg: #F5F5F7;
  --card: #FFFFFF;
  --ink: #1D1D1F;
  --sub: #6E6E73;
  --muted: #86868B;
  --line: rgba(0,0,0,0.07);
  --blue: #0071E3;
  --green-bg: #E8F8EE;
  --green-fg: #146C2E;
  --blue-bg: #EEF5FF;
  --blue-fg: #0058B9;
  --amber-bg: #FFF6E5;
  --amber-fg: #9A5B00;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", Inter, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.nav {
  position: sticky;
  top: 0;
  z-index: 20;
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  background: rgba(245, 245, 247, 0.82);
  border-bottom: 1px solid var(--line);
}
.nav-inner {
  max-width: 980px;
  margin: 0 auto;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.brand {
  font-weight: 600;
  font-size: 16px;
  letter-spacing: -0.015em;
  display: flex;
  align-items: center;
  gap: 8px;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--blue);
}
.wrap {
  max-width: 980px;
  margin: 0 auto;
  padding: 36px 24px 72px;
}
.hero h1 {
  font-size: 40px;
  line-height: 1.08;
  letter-spacing: -0.03em;
  font-weight: 700;
  margin: 0 0 10px;
}
.hero p {
  font-size: 18px;
  color: var(--sub);
  margin: 0 0 28px;
  max-width: 680px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}
.stat {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 18px 20px;
}
.stat-val {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.025em;
}
.stat-lbl {
  font-size: 13px;
  color: var(--sub);
  margin-top: 2px;
}
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
.pill-btn {
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--ink);
  padding: 7px 15px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.pill-btn.active {
  background: var(--ink);
  color: #fff;
  border-color: var(--ink);
}
.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 22px 24px;
  margin-bottom: 14px;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.card:hover {
  box-shadow: 0 8px 24px rgba(0,0,0,0.04);
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.rank {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  background: #F0F0F3;
  border-radius: 10px;
  padding: 4px 10px;
}
.title {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.badge {
  font-size: 12px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
}
.badge.post_first { background: var(--green-bg); color: var(--green-fg); }
.badge.good { background: var(--blue-bg); color: var(--blue-fg); }
.badge.weak { background: var(--amber-bg); color: var(--amber-fg); }
.meta {
  font-size: 13px;
  color: var(--sub);
  font-variant-numeric: tabular-nums;
}
.score-box {
  margin-left: auto;
  text-align: right;
}
.score-num {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.score-sub {
  font-size: 11px;
  color: var(--muted);
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}
@media (max-width: 680px) { .cols { grid-template-columns: 1fr; } }
.lbl {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin-bottom: 4px;
}
.quote {
  margin-top: 6px;
  font-size: 13.5px;
  color: var(--sub);
  font-style: italic;
}
.why-bar {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13.5px;
  color: var(--sub);
}
.dots {
  display: flex;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
}
.dot-pill {
  background: #F5F5F7;
  padding: 3px 9px;
  border-radius: 8px;
}
details {
  margin-top: 10px;
}
details summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: var(--blue);
  user-select: none;
}
pre {
  white-space: pre-wrap;
  background: #F8F8FA;
  border: 1px solid var(--line);
  padding: 14px;
  border-radius: 12px;
  font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  max-height: 280px;
  overflow: auto;
  margin-top: 10px;
}
"""


def render_html(manifest, ranked, alternates, rejected, q, by_num):
    e = lambda s: html.escape(str(s if s is not None else ""))
    m = q["blind_critic_means"]
    tb = (m.get("tool_best_per_episode") or {}).get("quality_0_100", "—")
    be = (m.get("baseline_ending") or {}).get("quality_0_100", "—")
    duel = q.get("tool_best_vs_episode_ending_baseline") or {}
    w_cnt = duel.get("tool_wins", 0)
    t_cnt = duel.get("ties", 0)
    l_cnt = duel.get("baseline_wins", 0)
    tiers = q.get("tiers") or {}
    pf_cnt = tiers.get("post_first", 0)
    gd_cnt = tiers.get("good", 0)
    wk_cnt = tiers.get("weak", 0)

    tier_label = {"post_first": "Post First", "good": "Good", "weak": "Needs Review"}

    parts = [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Kuku Promo Lab — {e(manifest['show'])}</title><style>{CSS}</style></head><body>",
        f"<nav class='nav'><div class='nav-inner'>"
        f"<div class='brand'><span class='dot'></span>Kuku Promo Lab</div>"
        f"<div class='meta'>{manifest['input']['episodes_processed']} episodes · {len(ranked)} ranked promos</div>"
        f"</div></nav>",
        f"<main class='wrap'><section class='hero'>"
        f"<h1>{e(manifest['show'])}</h1>"
        f"<p>Ranked 20–60 second promo cuts with exact timestamps, 3-second opening hooks, unresolved cliffhangers, and blind cold-viewer scores.</p>"
        f"</section>",
        f"<section class='stats'>"
        f"<div class='stat'><div class='stat-val'>{len(ranked)}</div><div class='stat-lbl'>Ranked Promos ({pf_cnt} Post First · {gd_cnt} Good)</div></div>"
        f"<div class='stat'><div class='stat-val'>{e(tb)} <span style='font-size:16px;font-weight:500;color:#6E6E73'>vs {e(be)}</span></div><div class='stat-lbl'>Blind Cold-Viewer Score vs Episode Endings ({w_cnt}W–{t_cnt}T–{l_cnt}L)</div></div>"
        f"<div class='stat'><div class='stat-val'>20–60s</div><div class='stat-lbl'>100% Rule-Checked · Speech-Safe Cuts</div></div>"
        f"<div class='stat'><div class='stat-val'>{len(rejected)}</div><div class='stat-lbl'>Rejected by Hard Rules · {len(alternates)} Alternates</div></div>"
        f"</section>",
        f"<div class='filters'>"
        f"<button class='pill-btn active' onclick=\"filterTier('all', this)\">All ({len(ranked)})</button>"
        f"<button class='pill-btn' onclick=\"filterTier('post_first', this)\">Post First ({pf_cnt})</button>"
        f"<button class='pill-btn' onclick=\"filterTier('good', this)\">Good ({gd_cnt})</button>"
        f"<button class='pill-btn' onclick=\"filterTier('weak', this)\">Needs Review ({wk_cnt})</button>"
        f"</div><section id='cards'>"
    ]

    for c in ranked:
        cr = c.get("critic") or {}
        segs = [Segment(**s) for s in c["segments"]]
        tier = c.get("tier", "good")
        t_lbl = tier_label.get(tier, tier)
        parts.append(
            f"<article class='card' data-tier='{e(tier)}'>"
            f"<div class='card-head'>"
            f"<span class='rank'>#{c['rank']}</span>"
            f"<span class='title'>{e(c['title'])}</span>"
            f"<span class='badge {e(tier)}'>{e(t_lbl)}</span>"
            f"<span class='meta'>{e(seg_text(c['segments']))} · {c['duration_ms'] / 1000:.1f}s</span>"
            f"<div class='score-box'><div class='score-num'>{c['score']}</div><div class='score-sub'>/ 100</div></div>"
            f"</div>"
            f"<div class='cols'>"
            f"<div><div class='lbl'>Opening Hook (0–3s)</div><div>{e(c['hook'])}</div>"
            f"<div class='quote'>“{e(c['hook_quote'])}”</div></div>"
            f"<div><div class='lbl'>Unresolved Cliffhanger</div><div>{e(c['cliffhanger'])}</div>"
            f"<div class='quote'>“{e(c['cliffhanger_quote'])}”</div></div>"
            f"</div>"
            f"<div class='why-bar'>"
            f"<div><strong style='color:#1D1D1F'>Why it works:</strong> {e(c['why_it_works'])}</div>"
            f"<div class='dots'>"
            f"<span class='dot-pill'>Hook {e(cr.get('hook'))}/5</span>"
            f"<span class='dot-pill'>Cliff {e(cr.get('cliffhanger'))}/5</span>"
            f"<span class='dot-pill'>Clear {e(cr.get('standalone'))}/5</span>"
            f"<span class='dot-pill'>Tap {e(cr.get('tap_to_watch'))}/5</span>"
            f"</div></div>"
            f"<details><summary>View transcript clip &amp; cold-viewer notes</summary>"
            f"<div style='margin-top:8px;font-size:13px;color:#6E6E73'>"
            f"<strong>Cold viewer understood:</strong> {e(cr.get('understood'))} · "
            f"<strong>Note:</strong> {e(cr.get('main_problem'))}</div>"
            f"<pre>{e(clip_script(by_num, segs))}</pre></details>"
            f"</article>"
        )

    parts.append(
        "</section>"
        "<script>"
        "function filterTier(tier, btn){"
        "document.querySelectorAll('.pill-btn').forEach(b=>b.classList.remove('active'));"
        "btn.classList.add('active');"
        "document.querySelectorAll('#cards .card').forEach(c=>{"
        "c.style.display=(tier==='all'||c.dataset.tier===tier)?'block':'none';"
        "});"
        "}"
        "</script></main></body></html>"
    )
    return "".join(parts)
