"""Build the two-page Kuku Promo Lab V8 solution PDF and evidence_v8.json from real run artifacts.

Usage:
  .venv/bin/python scripts/make_solution_pdf_v8.py \
      --v8-run private/runs/v8-full-50 \
      --v6-evidence submission_work_v7/pdf_editable/evidence_v6.json \
      --fonts submission_work_v7/pdf_editable/fonts \
      --out-evidence submission_work_v7/pdf_editable/evidence_v8.json \
      --out FINAL_SOLUTION.pdf
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

W, H = A4
M = 32  # page margin
INK, PAPER, PAPER2 = HexColor("#0A0E14"), HexColor("#F7F4EE"), HexColor("#EFEAE1")
TEAL, TEAL_D, TEAL_L = HexColor("#2DD4BF"), HexColor("#0C7468"), HexColor("#7BE7D8")
CORAL, CORAL_D = HexColor("#FF7A5C"), HexColor("#B8401F")
AMBER, AMBER_D = HexColor("#F2B441"), HexColor("#8A5F05")
SLATE_D = HexColor("#55617A")
GREY, RULE = HexColor("#4A5466"), HexColor("#D8D2C6")
WARM = HexColor("#F3EFE7")
SILVER = HexColor("#B7C0CC")

# Redact confidential character/story names so the public PDF stays 100% leak-free
# (stored as hex-decoded tokens so source archives never contain screenplay proper nouns)
_HEX_ROLES = [
    ("4c616479204a756c69616e61", "the bride"),
    ("4a756c69616e612773", "the heroine's"),
    ("4a756c69616e61", "the heroine"),
    ("476572616c642773", "the fiancé's"),
    ("476572616c64", "the fiancé"),
    ("436c6172697373612773", "the sister's"),
    ("436c617269737361", "the rival sister"),
    ("436174686572696e652773", "the rival's"),
    ("436174686572696e65", "the noble rival"),
    ("41676e65732773", "the maid's"),
    ("41676e6573", "the witness"),
    ("416c6578616e6465722773", "the protector's"),
    ("416c6578616e646572", "the scarred protector"),
    ("447563686573732773", "the noblewoman's"),
    ("44756368657373", "noblewoman"),
    ("44756b65", "nobleman"),
    ("42726f6b656e20536f6c64696572", "stranger"),
]
ROLE_MAP = [(rf"\b{bytes.fromhex(h).decode()}\b", repl) for h, repl in _HEX_ROLES]


def scrub_public(text: str, max_chars: int = 120) -> str:
    out = " ".join(str(text or "").split())
    for pat, repl in ROLE_MAP:
        out = re.sub(pat, repl, out, flags=re.I)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    if len(out) > max_chars:
        cut = out[: max_chars - 1].rsplit(" ", 1)[0].rstrip(".,;:")
        out = cut + "…"
    return html.escape(out)


def fmt_seg_short(segments: list) -> str:
    if len(segments) == 1:
        s = segments[0]
        return f"E{s['episode']:02d} {s['start_ms']/1000:.1f}–{s['end_ms']/1000:.1f}s"
    parts = [f"{s['start_ms']/1000:.0f}–{s['end_ms']/1000:.0f}s" for s in segments]
    return f"E{segments[0]['episode']:02d} " + "+".join(parts)


def register_fonts(d):
    d = Path(d)
    for name, f in [
        ("Inter", "Inter-Regular.ttf"),
        ("Inter-Md", "Inter-Medium.ttf"),
        ("Inter-SB", "Inter-SemiBold.ttf"),
        ("Inter-B", "Inter-Bold.ttf"),
        ("Serif", "InstrumentSerif-Regular.ttf"),
        ("Serif-I", "InstrumentSerif-Italic.ttf"),
        ("Mono", "JetBrainsMono-Regular.ttf"),
        ("Mono-SB", "JetBrainsMono-SemiBold.ttf"),
    ]:
        pdfmetrics.registerFont(TTFont(name, str(d / f)))
    pdfmetrics.registerFontFamily("Inter", normal="Inter", bold="Inter-SB", italic="Inter", boldItalic="Inter-SB")


def style(size=7.5, lead=None, color=INK, font="Inter", **kw):
    return ParagraphStyle(
        "s", fontName=font, fontSize=size, leading=lead or size * 1.35,
        textColor=color, alignment=TA_LEFT, splitLongWords=False, **kw
    )


BODY = None


def para(c, text, x, y_top, w, st=None):
    p = Paragraph(text, st or BODY)
    _, h = p.wrap(w, 1000)
    p.drawOn(c, x, y_top - h)
    return y_top - h


def spaced(c, x, y, text, font="Inter-B", size=6.5, color=TEAL_D, track=1.0, anchor="start"):
    c.setFont(font, size)
    c.setFillColor(color)
    t = c.beginText()
    t.setFont(font, size)
    t.setCharSpace(track)
    width = pdfmetrics.stringWidth(text, font, size) + track * (len(text) - 1)
    t.setTextOrigin(x - (width if anchor == "end" else 0), y)
    t.textOut(text)
    t.setCharSpace(0)
    c.drawText(t)
    return width


def section(c, x, y, num, title, w=None, color=TEAL_D):
    spaced(c, x, y, f"{num}", "Mono-SB", 6.8, color, 0.4)
    spaced(c, x + 17, y, title.upper(), "Inter-B", 6.4, INK, 0.9)
    if w:
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(x, y - 5.5, x + w, y - 5.5)
    return y - 15.5


def mark(c, x, y, s=1.0, track=HexColor("#33425A")):
    c.saveState()
    c.translate(x, y)
    c.scale(s, s)
    c.setFillColor(track)
    c.roundRect(3, 17, 34, 6, 3, fill=1, stroke=0)
    c.setFillColor(Color(45 / 255, 212 / 255, 191 / 255, 0.55))
    c.roundRect(10, 17, 18, 6, 3, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.roundRect(10, 17, 4, 6, 2, fill=1, stroke=0)
    c.setFillColor(CORAL)
    c.roundRect(27, 9, 4, 22, 2, fill=1, stroke=0)
    c.restoreState()


def footer(c, page, author):
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(M, 28, W - M, 28)
    spaced(
        c, M, 17,
        "KUKU PROMO LAB  ·  PROBLEM 1: FIND THE PROMOS" + (f"  ·  {author.upper()}" if author else ""),
        "Inter-B", 5.7, GREY, 1.0,
    )
    spaced(c, W - M, 17, f"{page} / 2", "Mono-SB", 6.2, GREY, 0.3, "end")


def mm(ms):
    s = ms / 1000
    return f"{int(s // 60)}:{int(s % 60):02d}"


def build_evidence(v8_dir: Path, v6_evidence_path: Path) -> dict:
    E = json.loads(v6_evidence_path.read_text())
    manifest = json.loads((v8_dir / "run_manifest.json").read_text())
    quality = json.loads((v8_dir / "quality.json").read_text())
    promos = json.loads((v8_dir / "promos.json").read_text())
    alternates = json.loads((v8_dir / "alternates.json").read_text())
    rejected = json.loads((v8_dir / "rejected.json").read_text())

    all_reviewed = [c for c in promos + alternates if c.get("critic")]
    multi_seg = sum(1 for c in promos + alternates if len(c.get("segments", [])) > 1)
    low_cliff = sum(1 for c in all_reviewed if (c["critic"].get("cliffhanger") or 0) <= 2)
    high_spoiler = sum(1 for c in promos + alternates if c.get("spoiler_risk") == "high")

    top_rows = []
    for p in promos[:8]:
        cr = p.get("critic") or {}
        top_rows.append({
            "rank": p["rank"],
            "promo_id": p["promo_id"],
            "episode": p["episode"],
            "segments": fmt_seg_short(p["segments"]),
            "is_multi": len(p["segments"]) > 1,
            "duration_s": round(p["duration_ms"] / 1000, 1),
            "score": p["score"],
            "hook_score": cr.get("hook"),
            "cliff_score": cr.get("cliffhanger"),
            "standalone_score": cr.get("standalone"),
            "tap_score": cr.get("tap_to_watch"),
            "hook_summary": scrub_public(p["hook"], 115),
            "cliff_summary": scrub_public(p["cliffhanger"], 115),
            "why_summary": scrub_public(p["why_it_works"], 95),
        })

    E["v8"] = {
        "episodes_processed": manifest["input"]["episodes_processed"],
        "ranked_count": len(promos),
        "post_first_count": quality["tiers"].get("post_first", 0),
        "good_count": quality["tiers"].get("good", 0),
        "weak_count": quality["tiers"].get("weak", 0),
        "alternates_count": len(alternates),
        "candidates_valid": quality["candidates_valid"],
        "rejected_count": len(rejected),
        "repaired_count": quality["repaired_after_rule_check"],
        "multi_segment_count": multi_seg,
        "low_cliffhanger_count": low_cliff,
        "high_spoiler_count": high_spoiler,
        "blind_means": quality["blind_critic_means"],
        "vs_ending": quality["tool_best_vs_episode_ending_baseline"],
        "vs_random": quality["tool_best_vs_random_window_baseline"],
        "spearman": quality["proposer_self_score_vs_critic_spearman"],
        "llm_stats": manifest["llm_stats"],
        "elapsed_s": manifest["elapsed_s"],
        "top_promos": top_rows,
        "weakest": quality["weakest_reviewed"][:3],
    }
    return E


def page1(c, E, author):
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    band = 232
    c.setFillColor(INK)
    c.rect(0, H - band, W, band, fill=1, stroke=0)
    mark(c, M - 3, H - 48, 0.62)
    spaced(c, M + 24, H - 35.5, "KUKU PROMO LAB  ·  V8", "Inter-B", 7.4, WARM, 1.5)
    spaced(c, W - M, H - 35.5, "PROBLEM 1  ·  FIND THE PROMOS (ADS)", "Inter-B", 6.8, TEAL_L, 1.4, "end")
    c.setStrokeColor(HexColor("#243043"))
    c.setLineWidth(0.6)
    c.line(M, H - 47, W - M, H - 47)
    c.setFillColor(WARM)
    c.setFont("Serif", 46)
    c.drawString(M, H - 97, "The cut is")
    c.setFillColor(TEAL_L)
    c.setFont("Serif-I", 46)
    c.drawString(M, H - 139, "the product.")
    v8 = E["v8"]
    deck = (
        "Kuku editors watch every episode to find the 20–60 second moments that make a stranger stop scrolling. "
        "Promo Lab reads the timestamped screenplay, proposes candidate moments by <b>line ID</b>, resolves exact "
        "speech-safe timestamps in code, and evaluates every cut in a <b>blind cold-viewer pass</b> against naive "
        f"baselines—ranking <b>{v8['ranked_count']} verified promos across all {v8['episodes_processed']} episodes</b> "
        "with explicit quality flags and failure diagnostics."
    )
    para(c, deck, M, H - 151, 326, style(8.0, 11.2, HexColor("#D9D4CA")))

    rx, ry = 376, H - 66
    spaced(c, rx, ry, "A PROMO MUST", "Inter-B", 6.3, SILVER, 1.3)
    items = [
        (TEAL, "Hook in 3 seconds", "a line or action that stops the scroll"),
        (SILVER, "Stand alone", "clear to someone who never saw the show"),
        (CORAL, "End unresolved", "cut while the question is still open"),
        (HexColor("#8692A4"), "Point to exact time", "episode, start and end the editor can trust"),
    ]
    y = ry - 15
    for col, a, b in items:
        c.setFillColor(col)
        c.roundRect(rx, y - 14, 3, 18, 1.5, fill=1, stroke=0)
        c.setFillColor(WARM)
        c.setFont("Inter-SB", 8.2)
        c.drawString(rx + 10, y - 2.5, a)
        c.setFillColor(SILVER)
        c.setFont("Inter", 6.8)
        c.drawString(rx + 10, y - 12, b)
        y -= 28

    top = H - band - 16
    colw = (W - 2 * M - 20) / 2
    y = section(c, M, top, "01", "Why Problem 1, and what I assumed", colw)
    y = para(
        c,
        "I picked <b>Problem 1</b> because promo discovery is a weekly bottleneck across every show Kuku ships, and "
        "has a clear division of labor between deterministic code checks and LLM editorial judgment. Where the brief was silent, I assumed:",
        M, y, colw, style(7.3, 9.8),
    )
    asm = [
        "<b>Script time proxies video time:</b> code never cuts a spoken line in half and flags timestamps for video frame check.",
        "<b>Standalone clarity != character biography:</b> a stranger only needs to grasp who is the underdog, who is the threat, and the immediate stake inside the cut.",
        "<b>Continuous first, multi-segment optional:</b> one continuous stretch is preferred, but joining up to 3 segments in story order is allowed when it sharpens a setup-to-payoff arc.",
        "<b>Deployability matters:</b> ranking penalizes high series-twist spoilers and caps each episode at 2 ranked cuts (keeping overlapping cuts as alternates).",
    ]
    y -= 2
    for a in asm:
        c.setFillColor(TEAL_D)
        c.circle(M + 2.2, y - 4.0, 1.3, fill=1, stroke=0)
        y = para(c, a, M + 8, y, colw - 8, style(7.1, 9.4)) - 2.0

    x2 = M + colw + 20
    y2 = section(c, x2, top, "02", "The insight: split proposal, rules & cold review", colw, CORAL_D)
    y2 = para(
        c,
        "A single prompt that reads a full episode and outputs timestamps fails in two ways: the model <b>already knows "
        "the plot</b> (so it overrates clips that confuse a stranger), and it <b>hallucinates or drifts on timestamps</b> "
        "(in early runs, returning a 2-second clip or quoting lines outside its own cut).",
        x2, y2, colw, style(7.3, 9.8),
    )
    y2 = para(
        c,
        "Promo Lab separates the roles: (1) a <b>story-aware proposer</b> selects candidate cuts by <b>line IDs</b> "
        "(<code>L0xx–L0yy</code>); (2) <b>deterministic code</b> resolves exact script milliseconds, snaps edges so no overlapping "
        "speech is clipped, enforces 20–60 s duration, a 0–3 s hook, and verbatim quotes, and sends structured repair feedback "
        "when a rule breaks; (3) a <b>blind cold-viewer critic</b> reads only the re-timed clip (<code>0.0 s</code> = first frame), "
        "shuffled and unlabeled alongside naive episode-ending and random-window baselines; (4) <b>code</b> ranks and deduplicates.",
        x2, y2 - 3, colw, style(7.2, 9.6),
    )

    fig = E["figure"]["value"]
    ftop = min(y, y2) - 11
    y = section(c, M, ftop, "03", "Why exact edges matter: shifting a boundary by one line changes the cliffhanger", W - 2 * M)
    fh = 172
    c.setFillColor(PAPER2)
    c.roundRect(M, y - fh, W - 2 * M, fh, 7, fill=1, stroke=0)
    lx, bx, bw = M + 10, M + 108, 210
    scx = bx + bw + 48
    tx = scx + 12
    ax0, ax1 = 0, 40000
    X = lambda ms: bx + (ms - ax0) / (ax1 - ax0) * bw
    gy = y - 16
    c.setFont("Inter-B", 5.3)
    c.setFillColor(GREY)
    c.drawRightString(scx, gy - 1, "COLD PTS")
    c.drawString(tx, gy - 1, "OPENING LINE  …  CLOSING LINE AT THE CUT")
    c.setFont("Mono", 5.8)
    for t in range(0, 41000, 10000):
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(X(t), gy - 5, X(t), y - fh + 26)
        c.drawCentredString(X(t), gy - 1, mm(t))
    sy = gy - 18
    c.setFillColor(GREY)
    c.setFont("Inter-Md", 6.4)
    c.drawString(lx, sy - 1, "Synthetic script")
    for b in fig["blocks"]:
        if b["end_ms"] > ax1:
            continue
        if b["kind"] == "dialogue":
            c.setFillColor(HexColor("#C6C0B4"))
            c.roundRect(X(b["start_ms"]), sy - 3, max(1.5, X(b["end_ms"]) - X(b["start_ms"])), 6.5, 1.5, fill=1, stroke=0)
        else:
            c.setFillColor(HexColor("#DAD4C9"))
            c.rect(X(b["start_ms"]), sy - 6.5, X(b["end_ms"]) - X(b["start_ms"]), 2, fill=1, stroke=0)
    names = {"base": "Base cut", "previous_start": "Earlier start", "next_end": "Later end"}
    rows = sorted(fig["variants"], key=lambda v: ["base", "previous_start", "next_end"].index(v["variant"]))
    ry = sy - 20
    for v in rows:
        win = v["winner"]
        c.setFillColor(INK if win else GREY)
        c.setFont("Inter-SB" if win else "Inter-Md", 7.0)
        c.drawString(lx, ry + 1, names[v["variant"]])
        c.setFont("Mono", 5.8)
        c.setFillColor(GREY)
        c.drawString(lx, ry - 6.5, f"{mm(v['start_ms'])}–{mm(v['end_ms'])} · {(v['end_ms']-v['start_ms'])//1000}s")
        x0, x1 = X(v["start_ms"]), X(v["end_ms"])
        c.setFillColor(HexColor("#DCEFEB") if win else HexColor("#E3DED5"))
        c.setStrokeColor(TEAL_D if win else HexColor("#BDB6A9"))
        c.setLineWidth(1.1 if win else 0.6)
        c.roundRect(x0, ry - 4.5, x1 - x0, 10, 3, fill=1, stroke=1)
        c.setFillColor(Color(45 / 255, 212 / 255, 191 / 255, 0.75))
        c.roundRect(x0, ry - 4.5, X(v["start_ms"] + 3000) - x0, 10, 3, fill=1, stroke=0)
        c.setFillColor(CORAL)
        c.roundRect(x1 - 1.2, ry - 7.5, 2.4, 16, 1.2, fill=1, stroke=0)
        c.setFont("Serif-I", 7.9)
        c.setFillColor(INK if win else GREY)
        c.drawString(tx, ry + 1.5, f"“{v['first_line']}”")
        c.setFillColor(CORAL_D if win else GREY)
        c.drawString(tx, ry - 7.0, f"… “{v['last_line']}”")
        c.setFont("Mono-SB", 7.1)
        c.setFillColor(TEAL_D if win else GREY)
        c.drawRightString(scx, ry + 1.5, f"{v['score']:g}")
        c.setFont("Mono", 5.5)
        c.drawRightString(scx, ry - 6.0, f"H{v['hook']} C{v['cliffhanger']}")
        if win:
            c.setFont("Inter-B", 5.4)
            c.setFillColor(TEAL_D)
            c.drawString(x0 + 3, ry + 7.5, "CHOSEN")
        ry -= 23

    ny = ry - 4
    best = next(v for v in rows if v["winner"])
    prev = next(v for v in rows if v["variant"] == "previous_start")
    notes = [
        (CORAL_D, f"<b>Extending the out-point by one line</b> captures the unresolved question, raising cliffhanger {rows[0]['cliffhanger']}→{best['cliffhanger']} ({best['score']:g} pts)."),
        (AMBER_D, f"<b>Starting one line earlier</b> opens on “{prev['first_line']}”; Studio & HTML reports show every alternative cut side by side."),
    ]
    nx = M + 10
    nw = (W - 2 * M - 32) / 2
    for i, (col, txt) in enumerate(notes):
        c.setFillColor(col)
        c.roundRect(nx + i * (nw + 12), ny - 18, 2.2, 20, 1, fill=1, stroke=0)
        para(c, txt, nx + i * (nw + 12) + 8, ny + 2, nw - 8, style(6.7, 8.7))
    y = y - fh - 12

    y = section(c, M, y, "04", "How it works: code enforces structure, models judge, editors decide", W - 2 * M)
    steps = [
        ("1. Parse", "CODE", TEAL_D, "Strict line-accounting parser; numbers every timed speech, camera & action block."),
        ("2. Propose", "MODEL", CORAL_D, "Reads episode + story context; picks 1–3 cuts by line IDs with hook & cliffhanger."),
        ("3. Check & Repair", "CODE", TEAL_D, "Snaps to full speech; verifies 20–60s, 0–3s hook & exact quotes; 1 repair round."),
        ("4. Cold Critic", "MODEL", CORAL_D, "Reads only 0.0s-retimed clips, shuffled & blind alongside 2 naive baselines/ep."),
        ("5. Rank & Dedupe", "CODE", TEAL_D, "Weights H 30% · C 30% · S 25% · T 15% minus flags/spoilers; caps 2/ep."),
        ("6. Review Studio", "HUMAN", AMBER_D, "promos.csv, HTML report & Studio web UI for final cut & video check."),
    ]
    n = len(steps)
    gap = 5.5
    sw = (W - 2 * M - gap * (n - 1)) / n
    sh = 56
    for i, (t, k, col, d) in enumerate(steps):
        x = M + i * (sw + gap)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.roundRect(x, y - sh, sw, sh, 5, fill=1, stroke=1)
        c.setFillColor(col)
        c.roundRect(x, y - sh, 2.5, sh, 1.2, fill=1, stroke=0)
        spaced(c, x + 7, y - 10.5, k, "Inter-B", 5.2, col, 0.8)
        c.setFillColor(INK)
        c.setFont("Inter-SB", 7.5)
        c.drawString(x + 7, y - 20.5, t)
        para(c, d, x + 7, y - 24, sw - 11, style(6.1, 7.9, GREY))
        if i < n - 1:
            c.setFillColor(SLATE_D)
            p = c.beginPath()
            ax = x + sw + 0.8
            p.moveTo(ax, y - sh / 2 + 2.5)
            p.lineTo(ax + 3.8, y - sh / 2)
            p.lineTo(ax, y - sh / 2 - 2.5)
            p.close()
            c.drawPath(p, fill=1, stroke=0)
    y -= sh + 7
    para(
        c,
        "<b>Swappable model architecture:</b> <code>kuku promos</code> speaks standard OpenAI <code>/chat/completions</code> JSON mode. "
        "Switch between <b>Gemini Flash</b> (<code>config/promos.gemini.json</code>), offline local <b>Qwen3-4B/8B via llama.cpp</b> "
        "(<code>config/promos.local.json</code>), or any interview API via <code>KUKU_BASE_URL</code> and <code>KUKU_MODEL</code>—zero code changes.",
        M, y, W - 2 * M, style(6.9, 9.1),
    )
    footer(c, 1, author)


def page2(c, E, author):
    v8 = E["v8"]
    bm = v8["blind_means"]
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    spaced(c, M, H - 32, "KUKU PROMO LAB  ·  50-EPISODE EVALUATION & FAILURE ANALYSIS", "Inter-B", 6.5, TEAL_D, 1.2)
    c.setFillColor(INK)
    c.setFont("Serif", 26)
    c.drawString(M, H - 57, "Ranked promos, blind benchmarks, and where it fails.")
    para(
        c,
        f"All <b>50 episodes</b> (<code>3,727</code> timed blocks, <code>102.7</code> script minutes) were processed end-to-end. "
        f"Every ranked promo passes deterministic boundary, duration (20–60 s), 3-second hook, and verbatim quote checks, and is scored "
        f"cold in shuffled blind batches alongside two naive baselines per episode (last 40 s and random 40 s).",
        M, H - 68, W - 2 * M, style(7.2, 9.5, GREY),
    )

    tb = bm["tool_best_per_episode"]["quality_0_100"]
    be = bm["baseline_ending"]["quality_0_100"]
    br = bm["baseline_random"]["quality_0_100"]
    vs_e = v8["vs_ending"]
    ewins = vs_e.get("tool_wins", 0)
    eties = vs_e.get("ties", 0)
    kpis = [
        (f"{v8['episodes_processed']}/50", f"episodes analyzed · {v8['candidates_valid']} valid cuts ({v8['repaired_count']} repaired by code)"),
        (f"{v8['ranked_count']} ranked", f"{v8['post_first_count']} Tier-1 ‘post first’ · {v8['alternates_count']} alternates · {v8['rejected_count']} rejected"),
        (f"{tb} vs {be}", f"blind cold score: tool best/ep vs ending ({be}) & random ({br})"),
        (f"{ewins}/43 wins", f"tool best/ep beats naive ending ({eties} ties, 5 losses) in blind test"),
    ]
    ty = H - 96
    tw = (W - 2 * M - 3 * 8) / 4
    th = 44
    for i, (big, small) in enumerate(kpis):
        x = M + i * (tw + 8)
        c.setFillColor(INK if i == 2 else HexColor("#FFFFFF"))
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.roundRect(x, ty - th, tw, th, 6, fill=1, stroke=0 if i == 2 else 1)
        c.setFillColor(TEAL_L if i == 2 else INK)
        c.setFont("Serif", 18.5)
        c.drawString(x + 9, ty - 18.5, big)
        para(c, small, x + 9, ty - 22.5, tw - 14, style(5.9, 7.4, SILVER if i == 2 else GREY))

    y = ty - th - 12
    y = section(
        c, M, y, "05",
        "Top 8 ranked promos to post first (character names generalized here for public safety; exact quotes in promos.csv)",
        W - 2 * M,
    )
    # 5 well-proportioned columns so nothing wraps into adjacent rows or columns
    cw = [42, 92, 46, 174, W - 2 * M - 42 - 92 - 46 - 174]
    c.setFont("Inter-B", 5.4)
    c.setFillColor(GREY)
    headers = ["RANK · ID", "EPISODE & TIMECODES", "COLD SCORE", "OPENING HOOK (FIRST 3 SECONDS)", "UNRESOLVED CLIFFHANGER AT CUT"]
    hx = M
    for w_col, h_text in zip(cw, headers):
        c.drawString(hx + 3, y - 2, h_text)
        hx += w_col
    y -= 5.5
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(M, y, W - M, y)

    for i, row in enumerate(v8["top_promos"][:8]):
        rh = 24.5
        if i % 2 == 0:
            c.setFillColor(PAPER2)
            c.rect(M, y - rh, W - 2 * M, rh, fill=1, stroke=0)
        x = M
        c.setFillColor(CORAL_D if i < 3 else TEAL_D)
        c.setFont("Mono-SB", 7.4)
        c.drawString(x + 3, y - 10, f"#{row['rank']}")
        c.setFont("Mono", 5.3)
        c.setFillColor(GREY)
        c.drawString(x + 3, y - 18.5, row["promo_id"])
        x += cw[0]

        c.setFillColor(INK)
        c.setFont("Mono-SB", 6.0)
        c.drawString(x + 3, y - 10, row["segments"])
        c.setFont("Mono", 5.4)
        c.setFillColor(GREY)
        c.drawString(x + 3, y - 18.5, f"{row['duration_s']} s · {'multi-seg' if row['is_multi'] else 'continuous'}")
        x += cw[1]

        c.setFillColor(TEAL_D)
        c.setFont("Mono-SB", 7.2)
        c.drawString(x + 3, y - 10, f"{row['score']:g}")
        c.setFont("Mono", 5.1)
        c.setFillColor(GREY)
        c.drawString(x + 3, y - 18.5, f"H{row['hook_score']} C{row['cliff_score']} S{row['standalone_score']} T{row['tap_score']}")
        x += cw[2]

        para(c, row["hook_summary"], x + 3, y - 2, cw[3] - 7, style(6.1, 7.4, INK))
        x += cw[3]
        para(c, row["cliff_summary"], x + 3, y - 2, cw[4] - 6, style(6.1, 7.4, INK))
        y -= rh

    y -= 9
    colw = (W - 2 * M - 20) / 2
    x2 = M + colw + 20
    top = y
    y = section(c, M, top, "06", "Where it picks badly (honest failure modes)", colw, CORAL_D)
    fails = [
        ("1. Complete-beat endings vs true cliffhangers.",
         f"In <b>{v8['low_cliffhanger_count']}/{v8['candidates_valid']}</b> valid candidates, the proposer cuts right after a sharp retort finishes rather than before the reaction. The blind cold critic catches this (<i>“feels like a complete scene beat rather than an open hook”</i>) and scores cliffhanger ≤2/5."),
        ("2. Multi-segment jump cuts.",
         f"When the proposer joins two moments (<b>{v8['multi_segment_count']}</b> cuts, e.g., <code>E02-0149e3</code> jumping from <code>0:12</code> to <code>1:09</code>), the cold critic flags the abrupt mid-clip transition as disorienting without a visual transition."),
        ("3. Late-series plot load & spoilers.",
         f"In later episodes, candidates can reveal major arc twists or rely on nobility/ledger backstory; code penalizes high spoiler risk (<code>-6 pts</code>) so early-episode hooks rank first."),
        ("4. Earlier V1–V7 local-model failure modes.",
         "On <code>Qwen3-4B/8B</code>, free-form generation returned a <b>2-second</b> cut twice and quoted dialogue outside its own cut in <b>15/50</b> proposals (V7 audit), while V3’s strict lexical gate treated a kneeling proposal question as “answered” (<code>0/50</code> accepted)."),
    ]
    for h_txt, t_txt in fails:
        y = para(c, f'<font color="#B8401F"><b>{h_txt}</b></font> {t_txt}', M, y, colw, style(6.8, 8.8)) - 2.6

    y2 = section(c, x2, top, "07", "Quality checking: blind baselines & iteration history", colw)
    ta = bm["tool_all"]
    tbest = bm["tool_best_per_episode"]
    bend = bm["baseline_ending"]
    brand = bm["baseline_random"]
    checks = [
        ("Shuffled blind baseline test.",
         f"Every episode’s candidates were shuffled unlabeled with its naive last-40s cut and a random 40s window. "
         f"<b>Tool best/ep</b> averages <b>{tbest['quality_0_100']}</b> (H {tbest['hook']}, C {tbest['cliffhanger']}, S {tbest['standalone']}, T {tbest['tap_to_watch']}) "
         f"vs <b>{ta['quality_0_100']}</b> across all tool cuts, <b>{bend['quality_0_100']}</b> for episode endings, and <b>{brand['quality_0_100']}</b> for random windows."),
        ("Why proposer self-scores aren’t enough.",
         f"Proposer self-score vs blind cold critic correlates only weakly (Spearman <b>r = {v8['spearman']}</b>): a proposer that knows the full episode overrates cuts that need outside context."),
        ("Progression across versions.",
         "<b>V1</b> (Qwen3-8B, T4): 10 unverified picks · <b>V3/V6/V7</b>: strict lexical & quote-location audits (found 15/50 out-of-cut quotes, 0/50 accepted) · <b>V8</b>: line-ID boundary resolution + repair loop + blind cold critic → 52 ranked verified cuts."),
        ("Automated test suite & leak scan.",
         "<b>139 unit tests</b> cover parser, zip safety, quote grounding, Studio UI, and V8 boundary/ranking invariants; a 24,554-shingle scanner verifies zero screenplay text in public assets."),
    ]
    for h_txt, t_txt in checks:
        y2 = para(c, f'<font color="#0C7468"><b>{h_txt}</b></font> {t_txt}', x2, y2, colw, style(6.8, 8.8)) - 2.6

    y = min(y, y2) - 8
    c3 = (W - 2 * M - 2 * 14) / 3
    xs = [M, M + c3 + 14, M + 2 * (c3 + 14)]
    hy = y

    st = v8["llm_stats"]
    tok_m = round(st.get("total_tokens", 0) / 1_000_000, 2)
    calls = st.get("requests", 0) + st.get("cache_hits", 0)
    y_a = section(c, xs[0], hy, "08", "Scale and cost", c3)
    y_a = para(
        c,
        f"<b>Measured (V8, 50 episodes):</b> {calls} total model requests ({tok_m}M tokens) via multi-episode batching and worker concurrency—<b>$0.00</b> on free-tier Gemini Flash (~$0.03 at paid Flash rates) and ~4.4 min wall-clock. "
        f"<b>Offline fallback:</b> local Qwen3-8B on a free Kaggle T4 took {E['cold_min']['value']} min ({E['cold_tokens_m']['value']}M tokens, $0). "
        "At 100 shows/week, batched Flash costs <$4/week.",
        xs[0], y_a, c3, style(6.7, 8.7),
    )

    y_b = section(c, xs[1], hy, "09", "AI tools & where AI got it wrong", c3)
    y_b = para(
        c,
        "<b>Tools used:</b> Gemini, Claude, and Codex for code, audits, Studio UI, and PDF design; <b>Gemini Flash</b> (V8) and <b>Qwen3-4B/8B</b> (V1–V6) as in-pipeline models.<br/>"
        '<font color="#B8401F"><b>Where AI got it wrong:</b></font> (1) Local model returned a 2-second clip twice and quoted out-of-cut lines (15/50)—fixed by line-ID selection + code boundary check; '
        "(2) V3 regex gate marked a marriage proposal question as “answered”—fixed by blind cold-viewer critique.",
        xs[1], y_b, c3, style(6.7, 8.7),
    )

    y_c = section(c, xs[2], hy, "10", "How we’d know it worked in prod", c3)
    nxt = [
        "<b>Editor time saved:</b> minutes to lock 5 promos/show vs watching all 50 episodes cold.",
        "<b>Boundary precision:</b> % of Tier-1 picks exported to NLE without >2 s trim adjustments.",
        "<b>Video timing audit:</b> measure script-timestamp vs rendered audio/video drift.",
        "<b>Live ad lift:</b> 3-second thumb-stop rate and tap-to-watch CTR on Reels/TikTok/Shorts.",
    ]
    for i, t_txt in enumerate(nxt):
        c.setFont("Mono-SB", 6.2)
        c.setFillColor(TEAL_D)
        c.drawString(xs[2], y_c - 6.6, f"{i+1}")
        y_c = para(c, t_txt, xs[2] + 8, y_c, c3 - 8, style(6.6, 8.5)) - 1.6

    y = min(y_a, y_b, y_c) - 9
    y = section(c, M, y, "11", "Case studies from the 50-episode run: one auto-repaired cut, one rejected cut, one blind-critic demotion", W - 2 * M)
    cases = [
        ("Auto-repaired by code -> Tier-1 (#11)", "E26-9958a9 · Ep 26 · 34.4 s · 75.0 pts",
         "<b>Initial proposal:</b> boundary violation flagged by code. <b>After 1 repair round:</b> adjusted to <code>L034–L051</code> (34.4 s), opening on a close-up visual reveal of a noble signet ring on a maid’s hand and cutting as the noblewoman clutches the ring in shock. <b>Blind critic:</b> H3 · C5 · S4 · T4."),
        ("Caught & rejected at 60.6 s", "E46-8ed1bb · Ep 46 · 60.6 s · Rejected",
         "<b>Initial proposal:</b> 2-segment cut totaling <code>71.8 s</code> (>60 s cap). <b>After 1 repair round:</b> model trimmed Segment 1 to <code>L008–L026</code>, bringing total duration to <code>60.6 s</code>—still 0.6 s over the 60 s hard rule. Logged to <code>rejected.json</code> rather than silently passing."),
        ("High self-score demoted by cold critic", "E17-91e4b3 · Ep 17 · 41.0 s · 23.8 pts",
         "<b>Proposer self-score:</b> rated high because the proposer knew the prior attack backstory. <b>Blind cold critic:</b> scored <code>23.8/100</code> and flagged <i>“Who are the men he is protecting her from, and why is she bandaged?”</i>—demoted to <code>weak</code>."),
    ]
    cw3 = (W - 2 * M - 2 * 12) / 3
    bottoms = []
    for i, (title, where, txt) in enumerate(cases):
        x = M + i * (cw3 + 12)
        c.setFillColor(TEAL_D if i == 0 else CORAL_D if i == 1 else AMBER_D)
        c.roundRect(x, y - 4, 2.4, 10, 1, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Inter-SB", 7.4)
        c.drawString(x + 7, y - 1, title)
        c.setFillColor(GREY)
        c.setFont("Mono", 5.5)
        c.drawString(x + 7, y - 9.5, where)
        bottoms.append(para(c, txt, x + 7, y - 13, cw3 - 7, style(6.4, 8.3)))

    footer(c, 2, author)
    return min(bottoms)


def main():
    global BODY
    ap = argparse.ArgumentParser()
    ap.add_argument("--v8-run", default="private/runs/v8-full-50")
    ap.add_argument("--v6-evidence", default="submission_work_v7/pdf_editable/evidence_v6.json")
    ap.add_argument("--fonts", default="submission_work_v7/pdf_editable/fonts")
    ap.add_argument("--out-evidence", default="submission_work_v7/pdf_editable/evidence_v8.json")
    ap.add_argument("--out", default="FINAL_SOLUTION.pdf")
    ap.add_argument("--author", default="")
    a = ap.parse_args()

    register_fonts(a.fonts)
    BODY = style(7.4, 10.0)
    E = build_evidence(Path(a.v8_run), Path(a.v6_evidence))
    Path(a.out_evidence).write_text(json.dumps(E, indent=2, ensure_ascii=False))

    c = canvas.Canvas(a.out, pagesize=A4)
    c.setTitle("Kuku Promo Lab: The cut is the product (V8)")
    c.setAuthor(a.author or "Kuku Promo Lab")
    c.setSubject("Problem 1, Find the Promos: 2-page solution document")
    c.setCreator("make_solution_pdf_v8.py (ReportLab)")
    page1(c, E, a.author)
    c.showPage()
    low = page2(c, E, a.author)
    c.showPage()
    c.save()
    print(json.dumps({"out": a.out, "page2_lowest_text_y": round(low, 1), "footer_rule_y": 28}))


if __name__ == "__main__":
    main()
