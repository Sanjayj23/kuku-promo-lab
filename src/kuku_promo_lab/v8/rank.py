"""Transparent ranking: cold-viewer scores, rule flags, spoiler risk, diversity.

score (0-100) = cold-critic quality, weighted to the brief's three criteria
  hook 30%, cliffhanger 30%, stands-alone 25%, would-tap 15%
minus penalties for warning flags and series spoilers. Overlapping cuts of the
same moment are collapsed to the best one; at most N promos per episode make
the ranked list, the rest are kept as alternates.
"""
from __future__ import annotations

from .cuts import Segment, overlap_ratio

WEIGHTS = {"hook": 0.30, "cliffhanger": 0.30, "standalone": 0.25, "tap_to_watch": 0.15}
PENALTIES = {"no_speech_or_action_in_first_3s": 8, "cliffhanger_line_not_at_the_cut": 4, "long_silent_tail": 4,
             "not_reviewed": 30}
SPOILER_PENALTY = {"high": 6, "medium": 2}


def quality(critic):
    if not critic:
        return 0.0
    raw = sum(w * float(critic.get(k) or 1) for k, w in WEIGHTS.items())
    return round((raw - 1) / 4 * 100, 1)


def score(c):
    q = quality(c.get("critic"))
    pen = sum(PENALTIES.get(f, 0) for f in c["flags"]) + SPOILER_PENALTY.get(c.get("spoiler_risk"), 0)
    return q, pen, round(q - pen, 1)


def why_ranked(c):
    cr = c.get("critic") or {}
    parts = [f"hook {cr.get('hook', '?')}/5", f"cliffhanger {cr.get('cliffhanger', '?')}/5",
             f"stands alone {cr.get('standalone', '?')}/5", f"would tap {cr.get('tap_to_watch', '?')}/5"]
    note = []
    if c.get("spoiler_risk") == "low":
        note.append("safe to post early (low spoiler risk)")
    elif c.get("spoiler_risk") == "high":
        note.append("reveals a major twist, so post later")
    if c["episode"] <= 10:
        note.append("early-episode setup matches where a new viewer starts")
    return "Cold viewer: " + ", ".join(parts) + (". " + "; ".join(note) if note else "") + "."


def rank_promos(candidates, cfg):
    for c in candidates:
        c["quality"], c["penalty"], c["score"] = score(c)
    pool = sorted(candidates, key=lambda c: (-c["score"], -(c.get("critic") or {}).get("hook", 0),
                                             c["episode"], c["proposer_rank"]))
    ranked, alternates, per_episode = [], [], {}
    for c in pool:
        segs = [Segment(**s) for s in c["segments"]]
        dup = next((r for r in ranked if overlap_ratio(segs, [Segment(**s) for s in r["segments"]]) > 0.5), None)
        if dup:
            c["alternate_reason"] = f"overlaps higher-ranked {dup['promo_id']} by more than 50%"
            alternates.append(c)
        elif per_episode.get(c["episode"], 0) >= cfg["max_per_episode_in_ranking"]:
            c["alternate_reason"] = f"episode already has {cfg['max_per_episode_in_ranking']} ranked promos"
            alternates.append(c)
        else:
            per_episode[c["episode"]] = per_episode.get(c["episode"], 0) + 1
            ranked.append(c)
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
        c["why_ranked"] = why_ranked(c)
        c["tier"] = ("post_first" if i <= cfg["top_n"] and c["score"] >= 60
                     else "good" if c["score"] >= 50 else "weak")
    return ranked, alternates

