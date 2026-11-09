"""V8 promo finder: propose (story-aware) -> check (code) -> review cold (blind) -> rank (code).

Run:  kuku promos --input <pack.zip|folder> --out <run dir> [--config config/promos.gemini.json]
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from ..ingest import read_pack
from . import prompts as P
from .cuts import Segment, check_candidate, clip_script, resolve_segment
from .llm import ChatClient, LLMError, load_dotenv
from .rank import rank_promos
from .report import write_outputs
from .transcript import load_episodes

PACK_LIMITS = {"max_files": 400, "max_member_bytes": 2_097_152, "max_total_bytes": 104_857_600,
               "max_compression_ratio": 100}

DEFAULTS = {
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "model": "gemini-3.5-flash",
    "fallback_models": [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3-flash-preview",
        "gemini-omni-1.2-flash",
    ],
    "api_key_env": "GEMINI_API_KEY",
    "temperature": 0.4,
    "critic_temperature": 0.2,
    "requests_per_minute": 10,
    "workers": 3,
    "episodes_per_call": 5,
    "max_attempts": 6,
    "timeout_s": 240,
    "max_candidates_per_episode": 3,
    "baseline_window_s": 40,
    "story_memory_episodes": 10,
    "top_n": 15,
    "max_per_episode_in_ranking": 2,
    "seed": 20261109,
    "cache_dir": "private/cache-v8",
}


def load_config(path=None):
    cfg = dict(DEFAULTS)
    if path:
        cfg.update(json.loads(Path(path).read_text()))
    return cfg


def show_title(files):
    first = files[0]["name"].split("/")
    return first[0] if len(first) > 1 else "Untitled show"


def story_context(all_docs, doc, window):
    prior = [d for d in all_docs if d.number < doc.number]
    if not prior:
        return "(this is Episode 1, the start of the series)"
    premise = prior[:2]
    recent = [d for d in prior[-window:] if d.number not in {p.number for p in premise}]
    lines = [f"E{d.number}: {d.brief()}" for d in premise]
    if recent and recent[0].number > premise[-1].number + 1:
        lines.append("...")
    lines += [f"E{d.number}: {d.brief()}" for d in recent]
    if doc.continuity:
        lines.append(f"Direct lead-in to E{doc.number}: {doc.continuity}")
    return "\n".join(lines)


def _single_propose_prompt(cfg, show, total, all_docs, doc):
    story = story_context(all_docs, doc, cfg["story_memory_episodes"])
    return story, P.PROPOSER_USER.format(
        show=show, number=doc.number, total=total, duration=doc.duration_ms / 1000,
        story=story, logline=doc.logline or "(none)", script=doc.render(),
        max_candidates=cfg["max_candidates_per_episode"],
    )


def run(input_path, out_dir, config_path=None, episodes=None, log=print):
    load_dotenv()
    cfg = load_config(config_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    files, inventory = read_pack(input_path, PACK_LIMITS)
    all_docs = load_episodes(files)
    docs = [d for d in all_docs if d.number in set(episodes)] if episodes else list(all_docs)
    by_num = {d.number: d for d in all_docs}
    show = show_title(files)
    client = ChatClient(cfg, out)
    batch_n = max(1, int(cfg.get("episodes_per_call", 1)))
    if len(docs) <= 3:
        batch_n = 1
    log(f"{show}: {len(docs)} episodes, model {client.model} (+{len(client.models)-1} fallbacks), "
        f"batch={batch_n}, workers={cfg['workers']}")

    proposals, rejected, summaries, failures, reviewed, baseline_rows = [], [], [], [], [], []
    log_lock = threading.Lock()

    # Partition into single-episode cached docs vs batchable docs
    single_docs, uncached_docs = [], []
    for doc in docs:
        _, user_prompt = _single_propose_prompt(cfg, show, len(files), all_docs, doc)
        if batch_n == 1 or client.has_cached("propose", P.PROPOSER_SYSTEM, user_prompt):
            single_docs.append(doc)
        else:
            uncached_docs.append(doc)

    batches = [[d] for d in single_docs] + [
        uncached_docs[i:i + batch_n] for i in range(0, len(uncached_docs), batch_n)
    ]

    def process_group(group):
        t0 = time.time()
        if len(group) == 1:
            doc = group[0]
            ep_rng = random.Random(cfg["seed"] + doc.number * 1009)
            story, _ = _single_propose_prompt(cfg, show, len(files), all_docs, doc)
            valid, bad, summary = propose(client, cfg, show, len(files), doc, story)
            baselines = make_baselines(doc, cfg, ep_rng)
            cands, bases = review_episode(client, cfg, by_num, doc, valid, baselines, ep_rng.random())
            with log_lock:
                log(f"  E{doc.number:02d} {len(valid)} valid, {len(bad)} rejected, reviewed ({time.time() - t0:.0f}s)")
            return [(doc.number, summary or doc.brief(), valid, bad, cands, bases)]
        return process_batch(client, cfg, show, len(files), all_docs, by_num, group, log, log_lock, t0)

    with ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
        futures = {pool.submit(process_group, grp): grp for grp in batches}
        results = []
        for fut in as_completed(futures):
            grp = futures[fut]
            try:
                results.extend(fut.result())
            except LLMError as exc:
                tag = f"E{grp[0].number:02d}-E{grp[-1].number:02d}"
                failures.append({"episodes": [d.number for d in grp], "stage": "batch", "error": str(exc)})
                with log_lock:
                    log(f"  {tag} FAILED: {exc}")

    for ep_num, summary, valid, bad, cands, bases in sorted(results, key=lambda r: r[0]):
        summaries.append(f"E{ep_num:02d}: {summary}")
        proposals += valid
        rejected += bad
        reviewed += cands
        baseline_rows += bases

    reviewed_ids = {c["promo_id"] for c in reviewed}
    for c in proposals:
        if c["promo_id"] not in reviewed_ids:
            c["flags"] = sorted(set(c["flags"] + ["not_reviewed"]))
            reviewed.append(c)

    ranked, alternates = rank_promos(reviewed, cfg)
    manifest = {
        "tool": "kuku-promo-lab v8", "prompt_version": P.PROMPT_VERSION,
        "started_utc": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - started, 1), "show": show,
        "input": {"path": str(input_path), "pack_hash": inventory["pack_hash"], "files": len(files),
                  "episodes_processed": len(docs)},
        "model": client.model, "base_url": client.base_url,
        "config": {k: v for k, v in cfg.items() if k != "api_key"},
        "llm_stats": client.stats, "failures": failures,
        "prompt_sha256": hashlib.sha256((P.PROPOSER_SYSTEM + P.PROPOSER_USER + P.CRITIC_SYSTEM + P.CRITIC_USER)
                                        .encode()).hexdigest(),
    }
    write_outputs(out, manifest, ranked, alternates, rejected, baseline_rows, summaries, by_num)
    log(f"done: {len(ranked)} ranked, {len(alternates)} alternates, {len(rejected)} rejected, "
        f"{client.stats['requests']} fresh calls, {client.stats['cache_hits']} cached, {manifest['elapsed_s']}s")
    return manifest


def process_batch(client, cfg, show, total, all_docs, by_num, group, log, log_lock, t0):
    tag = f"E{group[0].number:02d}-E{group[-1].number:02d}"
    story = story_context(all_docs, group[0], cfg["story_memory_episodes"])
    ep_blocks = []
    for doc in group:
        ep_blocks.append(
            f"=== EPISODE {doc.number} (duration {doc.duration_ms / 1000:.1f} s) ===\n"
            f"LOGLINE: {doc.logline or '(none)'}\n"
            f"CONTINUITY: {doc.continuity or '(none)'}\n"
            f"{doc.render()}"
        )
    user = P.PROPOSER_BATCH_USER.format(
        show=show, total=total, story=story, count=len(group),
        max_candidates=cfg["max_candidates_per_episode"],
        episodes_block="\n\n".join(ep_blocks),
    )

    def shape(parsed):
        eps = parsed.get("episodes")
        if not isinstance(eps, list) or len(eps) != len(group):
            raise ValueError(f"JSON must contain 'episodes' list of length {len(group)}")
        for entry in eps:
            if not isinstance(entry.get("candidates"), list) or not entry["candidates"]:
                raise ValueError(f"episode {entry.get('episode')} must have non-empty 'candidates'")

    parsed, _ = client.chat_json("propose_batch", P.PROPOSER_SYSTEM, user, validate=shape, call_id=f"{tag}-propose")
    by_ep = {int(e.get("episode", 0)): e for e in parsed["episodes"] if str(e.get("episode", "")).isdigit()}
    per_doc_items = []
    all_valid, all_bases = [], []
    for idx, doc in enumerate(group):
        entry = by_ep.get(doc.number) or parsed["episodes"][idx]
        summary = str(entry.get("episode_summary", "")).strip()[:400] or doc.brief()
        raw_cands = entry["candidates"][:cfg["max_candidates_per_episode"]]
        valid, bad = [], []
        for rank, cand in enumerate(raw_cands, 1):
            item = build(doc, cand, rank, "proposer")
            (valid if not item["problems"] else bad).append(item)
        if not valid and bad:
            # Repair single episode if every candidate in that episode had a rule violation
            ep_story = story_context(all_docs, doc, cfg["story_memory_episodes"])
            valid, bad, _ = _repair_episode(client, cfg, show, total, doc, ep_story, valid, bad)
        ep_rng = random.Random(cfg["seed"] + doc.number * 1009)
        bases = make_baselines(doc, cfg, ep_rng)
        all_valid.extend(valid)
        all_bases.extend(bases)
        per_doc_items.append((doc, summary, valid, bad, bases))

    # Blind cold-viewer review for the batch
    review_items(client, cfg, by_num, all_valid + all_bases, call_id=f"{tag}-critic",
                 shuffle_seed=cfg["seed"] + group[0].number * 31)
    out = []
    v_total, b_total = 0, 0
    for doc, summary, valid, bad, bases in per_doc_items:
        v_total += len(valid)
        b_total += len(bad)
        out.append((doc.number, summary, valid, bad, valid, bases))
    with log_lock:
        log(f"  {tag} ({len(group)} eps): {v_total} valid, {b_total} rejected, reviewed ({time.time() - t0:.0f}s)")
    return out


def _repair_episode(client, cfg, show, total, doc, story, valid, bad):
    user = P.PROPOSER_USER.format(show=show, number=doc.number, total=total, duration=doc.duration_ms / 1000,
                                  story=story, logline=doc.logline or "(none)", script=doc.render(),
                                  max_candidates=cfg["max_candidates_per_episode"])
    problems = "\n".join(f"- \"{b['title']}\": " + "; ".join(b["problems"]) for b in bad)
    messages_user = user + "\n\nYOUR PREVIOUS ANSWER:\n" + json.dumps({"candidates": [b["raw"] for b in bad]},
                                                                        ensure_ascii=False) \
        + "\n\n" + P.REPAIR_USER.format(problems=problems)

    def shape(parsed):
        if not isinstance(parsed.get("candidates"), list) or not parsed["candidates"]:
            raise ValueError("JSON must contain a non-empty 'candidates' list")

    try:
        fixed, _ = client.chat_json("repair", P.PROPOSER_SYSTEM, messages_user, validate=shape,
                                    call_id=f"E{doc.number:02d}-repair")
        still_bad = []
        for b, cand in zip(bad, fixed["candidates"]):
            item = build(doc, cand, b["proposer_rank"], "repaired")
            if item["problems"]:
                b["repair_problems"] = item["problems"]
                still_bad.append(b)
            else:
                item["flags"] = sorted(set(item["flags"] + ["repaired_after_rule_check"]))
                valid.append(item)
        still_bad += bad[len(fixed["candidates"]):]
        bad = still_bad
    except LLMError as exc:
        for b in bad:
            b["repair_problems"] = [f"repair call failed: {exc}"]
    return valid, bad, ""


def propose(client, cfg, show, total, doc, story):
    user = P.PROPOSER_USER.format(show=show, number=doc.number, total=total, duration=doc.duration_ms / 1000,
                                  story=story, logline=doc.logline or "(none)", script=doc.render(),
                                  max_candidates=cfg["max_candidates_per_episode"])

    def shape(parsed):
        if not isinstance(parsed.get("candidates"), list) or not parsed["candidates"]:
            raise ValueError("JSON must contain a non-empty 'candidates' list")

    parsed, _ = client.chat_json("propose", P.PROPOSER_SYSTEM, user, validate=shape, call_id=f"E{doc.number:02d}-propose")
    summary = str(parsed.get("episode_summary", "")).strip()[:400]
    raw = parsed["candidates"][:cfg["max_candidates_per_episode"]]
    valid, bad = [], []
    for rank, cand in enumerate(raw, 1):
        item = build(doc, cand, rank, "proposer")
        (valid if not item["problems"] else bad).append(item)
    if bad:
        valid, bad, _ = _repair_episode(client, cfg, show, total, doc, story, valid, bad)
    return valid, bad, summary


def build(doc, cand, rank, origin):
    segments, problems, flags = check_candidate(doc, cand)
    risk = str(cand.get("spoiler_risk", "")).lower().split()[0] if cand.get("spoiler_risk") else "unknown"
    item = {
        "promo_id": "", "episode": doc.number, "origin": origin, "proposer_rank": rank,
        "title": str(cand.get("title", "")).strip(), "hook": str(cand.get("hook", "")).strip(),
        "hook_line": cand.get("hook_line"), "hook_quote": cand.get("hook_quote", ""),
        "cliffhanger": str(cand.get("cliffhanger", "")).strip(), "cliffhanger_line": cand.get("cliffhanger_line"),
        "cliffhanger_quote": cand.get("cliffhanger_quote", ""),
        "why_it_works": str(cand.get("why_it_works", "")).strip(),
        "setup_needed": str(cand.get("setup_needed", "")).strip(), "spoiler_risk": risk.strip(" ,.|"),
        "self_score": cand.get("self_score"), "segments": [s.__dict__ for s in segments],
        "duration_ms": sum(s.duration_ms for s in segments), "problems": problems, "flags": flags, "raw": cand,
    }
    key = json.dumps([doc.number, [(s.start_ms, s.end_ms) for s in segments], item["title"]])
    item["promo_id"] = f"E{doc.number:02d}-" + hashlib.sha1(key.encode()).hexdigest()[:6]
    return item


def make_baselines(doc, cfg, rng):
    """Two naive cuts for blind comparison: the episode's last N s, and a random N s window."""
    window = cfg["baseline_window_s"] * 1000
    speech = [ln for ln in doc.lines if ln.is_speech] or doc.lines
    end_line = speech[-1]
    out = []
    for name, target_start in (("baseline_ending", end_line.end_ms - window),
                               ("baseline_random", rng.randint(0, max(0, doc.duration_ms - window)))):
        start_line = min(doc.lines, key=lambda ln: abs(ln.start_ms - target_start))
        later = [ln for ln in doc.lines if ln.end_ms <= start_line.start_ms + window and ln.start_ms >= start_line.start_ms]
        stop = (later or [start_line])[-1] if name == "baseline_random" else end_line
        try:
            seg = resolve_segment(doc, start_line.lid, stop.lid)
        except ValueError:
            continue
        out.append({"promo_id": f"E{doc.number:02d}-{name}", "episode": doc.number, "origin": name,
                    "segments": [seg.__dict__], "duration_ms": seg.duration_ms, "flags": [], "problems": []})
    return out


def review_items(client, cfg, by_num, items, call_id, shuffle_seed):
    if not items:
        return
    order = list(range(len(items)))
    random.Random(shuffle_seed).shuffle(order)
    labels = [chr(65 + i) if len(items) <= 26 else f"C{i + 1:02d}" for i in range(len(items))]
    blocks = []
    for pos, idx in enumerate(order):
        segs = [Segment(**s) for s in items[idx]["segments"]]
        blocks.append(f"=== CLIP {labels[pos]} ({items[idx]['duration_ms'] / 1000:.1f} s) ===\n"
                      + clip_script(by_num, segs))
    user = P.CRITIC_USER.format(clips="\n\n".join(blocks))

    def shape(parsed):
        reviews = parsed.get("reviews")
        if not isinstance(reviews, list) or len(reviews) != len(items):
            raise ValueError(f"need exactly {len(items)} reviews, one per clip")
        for r in reviews:
            for k in ("hook", "standalone", "cliffhanger", "tap_to_watch"):
                v = r.get(k)
                if not isinstance(v, (int, float)) or not 1 <= v <= 5:
                    raise ValueError(f"clip {r.get('clip')}: {k} must be a number 1-5")

    parsed, _ = client.chat_json("critic", P.CRITIC_SYSTEM, user, validate=shape,
                                 call_id=call_id, temperature=cfg["critic_temperature"])
    by_label = {str(r.get("clip", "")).strip().upper(): r for r in parsed["reviews"]}
    for pos, idx in enumerate(order):
        review = by_label.get(labels[pos]) or parsed["reviews"][pos]
        items[idx]["critic"] = {k: review.get(k) for k in ("first_3s", "hook", "understood", "confusions",
                                                           "standalone", "open_question", "cliffhanger",
                                                           "tap_to_watch", "main_problem")}
        items[idx]["critic"]["blind_label"] = labels[pos]


def review_episode(client, cfg, by_num, doc, candidates, baselines, shuffle_seed):
    """One blind, context-free critic call per episode. Clip order is shuffled; baselines are unlabeled."""
    items = candidates + baselines
    review_items(client, cfg, by_num, items, call_id=f"E{doc.number:02d}-critic", shuffle_seed=shuffle_seed)
    return candidates, baselines
