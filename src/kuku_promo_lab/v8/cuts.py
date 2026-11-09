"""Deterministic boundary resolution and rule checks for proposed promos.

The model chooses line IDs; this module turns them into exact script times,
never cuts a spoken line in half, and rejects anything that breaks the brief
(20-60 s, hook inside the first 3 s, cliffhanger inside the clip).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_MS, MAX_MS, HOOK_MS = 20_000, 60_000, 3_000
GRACE_MS = 200  # tolerance for overlapping timestamps in the script


@dataclass
class Segment:
    episode: int
    start_ms: int
    end_ms: int
    start_line: str
    end_line: str
    adjustments: list = field(default_factory=list)

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms


def norm(text):
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2014", " ").replace("\u2013", " ").replace("...", " ").replace("\u2026", " ")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text).split())


def resolve_segment(doc, start_lid, end_lid, *, min_end_ms=None, max_start_ms=None):
    index = {ln.lid: i for i, ln in enumerate(doc.lines)}
    if start_lid not in index or end_lid not in index:
        raise ValueError(f"unknown line id {start_lid if start_lid not in index else end_lid}")
    s, e = index[start_lid], index[end_lid]
    if e < s:
        raise ValueError(f"segment {start_lid}-{end_lid} ends before it starts")
    chosen = doc.lines[s:e + 1]
    start_ms = chosen[0].start_ms
    end_ms = max(ln.end_ms for ln in chosen)
    first_speech = next((ln.start_ms for ln in chosen if ln.is_speech), chosen[-1].end_ms)
    last_speech = max((ln.end_ms for ln in chosen if ln.is_speech), default=chosen[0].start_ms)
    cap_start = first_speech if max_start_ms is None else min(first_speech, max_start_ms)
    floor_end = last_speech if min_end_ms is None else max(last_speech, min_end_ms)
    adjustments = []
    # Do not bleed the tail of an earlier excluded speech line into the opening shot.
    for ln in doc.lines[:s]:
        if ln.is_speech and start_ms < ln.end_ms - GRACE_MS and ln.end_ms <= cap_start:
            adjustments.append(f"start advanced {ln.end_ms - start_ms} ms past {ln.lid}")
            start_ms = ln.end_ms
    # Do not let the next excluded speech line start before the cut.
    for ln in doc.lines[e + 1:]:
        if ln.is_speech and ln.start_ms + GRACE_MS < end_ms and ln.start_ms >= floor_end:
            adjustments.append(f"end trimmed {end_ms - ln.start_ms} ms before {ln.lid}")
            end_ms = ln.start_ms
            break
    return Segment(doc.number, start_ms, end_ms, start_lid, end_lid, adjustments)


def lines_in(doc, seg):
    """Lines a viewer actually sees/hears inside a segment."""
    index = {ln.lid: i for i, ln in enumerate(doc.lines)}
    if seg.start_line in index and seg.end_line in index:
        s, e = index[seg.start_line], index[seg.end_line]
        return [ln for ln in doc.lines[s:e + 1]
                if min(ln.end_ms, seg.end_ms) - max(ln.start_ms, seg.start_ms) > 0]
    out = []
    for ln in doc.lines:
        overlap = min(ln.end_ms, seg.end_ms) - max(ln.start_ms, seg.start_ms)
        if overlap > 0 and (overlap >= 0.5 * (ln.end_ms - ln.start_ms) or ln.start_ms >= seg.start_ms):
            out.append(ln)
    return out


def _match_quote_line(lines, quote):
    q = norm(quote)
    if not q:
        return None
    for ln in lines:
        if q in norm(ln.text):
            return ln
    return None


def check_candidate(doc, cand):
    """Return (segments, problems, flags). Problems are rule violations; flags are warnings."""
    problems, flags = [], []
    raw = cand.get("segments") or []
    if not isinstance(raw, list) or not raw:
        return [], ["no segments"], flags
    if len(raw) > 3:
        problems.append(f"{len(raw)} segments; at most 3 allowed")
    by_id = doc.by_id()
    index = {ln.lid: i for i, ln in enumerate(doc.lines)}
    hook = by_id.get(str(cand.get("hook_line", "")).strip())
    cliff = by_id.get(str(cand.get("cliffhanger_line", "")).strip())

    # Pre-align hook_line / cliffhanger_line to exact quote matches inside the chosen line range
    first_item, last_item = raw[0], raw[:3][-1]
    s0, e0 = index.get(str(first_item.get("start_line", "")).strip()), index.get(str(first_item.get("end_line", "")).strip())
    hook_quote = str(cand.get("hook_quote") or "")
    if hook_quote and s0 is not None and e0 is not None and s0 <= e0 and (hook is None or norm(hook_quote) not in norm(hook.text)):
        matched = _match_quote_line(doc.lines[s0:e0 + 1], hook_quote)
        if matched is not None:
            hook = matched
            cand["hook_line"] = matched.lid
            flags.append("hook_line_aligned_to_quote")

    sN, eN = index.get(str(last_item.get("start_line", "")).strip()), index.get(str(last_item.get("end_line", "")).strip())
    cliff_quote = str(cand.get("cliffhanger_quote") or "")
    if cliff_quote and sN is not None and eN is not None and sN <= eN and (cliff is None or norm(cliff_quote) not in norm(cliff.text)):
        matched = _match_quote_line(doc.lines[sN:eN + 1], cliff_quote)
        if matched is not None:
            cliff = matched
            cand["cliffhanger_line"] = matched.lid
            flags.append("cliffhanger_line_aligned_to_quote")

    segments = []
    for idx, item in enumerate(raw[:3]):
        try:
            segments.append(resolve_segment(
                doc, str(item.get("start_line", "")).strip(), str(item.get("end_line", "")).strip(),
                max_start_ms=hook.start_ms if idx == 0 and hook else None,
                min_end_ms=cliff.end_ms if idx == len(raw[:3]) - 1 and cliff else None,
            ))
        except (ValueError, AttributeError) as exc:
            problems.append(str(exc))
    if problems:
        return segments, problems, flags
    for a, b in zip(segments, segments[1:]):
        if b.start_ms < a.end_ms:
            problems.append("segments overlap or are out of story order")
    for seg in segments:
        if seg.duration_ms < 2_500:
            problems.append(f"segment {seg.start_line}-{seg.end_line} is only {seg.duration_ms / 1000:.1f} s")
        if seg.adjustments:
            flags.append("boundary_snapped_to_full_line")
    total = sum(s.duration_ms for s in segments)
    if total < MIN_MS or total > MAX_MS:
        problems.append(f"total duration {total / 1000:.1f} s is outside 20-60 s")

    first, last = segments[0], segments[-1]
    first_lines = lines_in(doc, first)
    last_lines = lines_in(doc, last)

    if hook is None:
        problems.append(f"hook_line {cand.get('hook_line')!r} is not a line id")
    elif not (first.start_ms - GRACE_MS <= hook.start_ms < first.start_ms + HOOK_MS):
        problems.append(f"hook_line {hook.lid} starts {(hook.start_ms - first.start_ms) / 1000:.1f} s into the promo, "
                        f"not within the first 3 s")
    if cliff is None:
        problems.append(f"cliffhanger_line {cand.get('cliffhanger_line')!r} is not a line id")
    elif not (last.start_ms - GRACE_MS <= cliff.start_ms and cliff.end_ms <= last.end_ms + GRACE_MS):
        problems.append(f"cliffhanger_line {cliff.lid} is outside the promo")
    elif last.end_ms - cliff.end_ms > 6_000:
        flags.append("cliffhanger_line_not_at_the_cut")

    for field_name, line in (("hook_quote", hook), ("cliffhanger_quote", cliff)):
        quote = norm(str(cand.get(field_name) or ""))
        if quote and line is not None and quote not in norm(line.text):
            problems.append(f"{field_name} is not an exact quote of {line.lid} ({line.text[:80]!r})")

    opening = [ln for ln in first_lines if first.start_ms - GRACE_MS <= ln.start_ms < first.start_ms + HOOK_MS]
    if not any(ln.is_speech or ln.kind == "action" for ln in opening):
        flags.append("no_speech_or_action_in_first_3s")
    tail = [ln for ln in last_lines if ln.is_speech]
    if tail and tail[-1].end_ms < last.end_ms - 8_000:
        flags.append("long_silent_tail")
    return segments, problems, sorted(set(flags))


def clip_script(docs_by_num, segments):
    """Render what a cold viewer sees, re-timed so 0.0 s is the clip's first frame."""
    out, offset = [], 0
    for i, seg in enumerate(segments):
        if i:
            out.append("[CUT]")
        doc = docs_by_num[seg.episode]
        for ln in lines_in(doc, seg):
            rs = max(0, ln.start_ms - seg.start_ms) + offset
            re_ = min(ln.end_ms, seg.end_ms) - seg.start_ms + offset
            text = " ".join(ln.text.split())
            if ln.is_speech:
                mood = f" ({ln.emotion})" if ln.emotion else ""
                body = f"{ln.speaker or 'VOICE'}{mood}: {text}"
            else:
                body = f"{'SCREEN' if ln.kind == 'camera' else ln.kind.upper()}: {text}"
            out.append(f"[{rs / 1000:.1f}-{re_ / 1000:.1f}] {body}")
        offset += seg.duration_ms
    return "\n".join(out)


def overlap_ratio(a_segments, b_segments):
    def span(segs):
        return [(s.episode, s.start_ms, s.end_ms) for s in segs]
    inter = 0
    for ea, sa, xa in span(a_segments):
        for eb, sb, xb in span(b_segments):
            if ea == eb:
                inter += max(0, min(xa, xb) - max(sa, sb))
    shorter = min(sum(s.duration_ms for s in a_segments), sum(s.duration_ms for s in b_segments))
    return inter / shorter if shorter else 0.0
